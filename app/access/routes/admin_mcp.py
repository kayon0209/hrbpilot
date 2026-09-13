"""外部 Agent 授权的管理端：列出安装实例与客户端，并撤销它们。

为什么必须先有它
----------------
撤销能力如果只存在于"用户自己点一下"，那么一次凭据泄漏的处置速度取决于那个用户
什么时候发现。管理员入口让"立刻切断某个客户端"成为运维动作，而不是等用户。

方案 §WP3 说"若 UI 延后，先提供受 RBAC 保护的 API" —— 本文件就是那个 API。

撤销粒度是两个，不是一个
------------------------
- **安装实例**（``family_id``）：一次授权会话及其整条 refresh 轮换链。用户想
  "把这个 Agent 从我账号里踢掉"时是这个粒度。
- **客户端**（``client_id``）：该客户端在**本租户**下的所有会话。运维想"这个客户端
  整体不可信"时是这个粒度。

两者不能互相替代：只有实例级的话，处置一个被攻破的客户端要逐条点；只有客户端级
的话，用户没法只踢掉自己那一个实例。

为什么不提供"撤销单个 access token"
----------------------------------
access token 是无状态 JWT，服务端不存它，只存它所在 family 的撤销记录。要精确作废
某一把 access token 就得按 jti 记一条 —— ``revoke_token`` 支持，但管理员手里通常
根本没有那把令牌（它在客户端那里），拿不到 jti。所以管理员面只暴露上面两个粒度，
jti 级留给 RFC 7009 撤销端点（调用方持有令牌时使用）。

审计
----
撤销本身必须留痕：否则"谁在什么时候把谁的授权撤了"不可追溯，而这个动作本身就是
一种高权限操作。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_session_factory
from app.data.models.oauth import OAuthClient, OAuthToken
from app.oauth.tokens import REVOKED_REASON_USER_REVOKED, revoke_family_in_own_session
from app.shared.audit import append_security_audit_event
from app.shared.errors import NotFoundError

router = APIRouter(prefix="/api/admin/mcp", tags=["admin-mcp"])


class InstallationOut(BaseModel):
    """一个安装实例（= 一条 refresh 轮换链）的最小视图。"""

    family_id: str
    client_id: str
    user_id: str
    role: str
    #: 未撤销的 refresh token 条数。为 0 表示已经没有可续期的凭据 ——
    #: 现有 access token 到期后这个实例即彻底失效。
    active_refresh_tokens: int
    created_at: datetime | None = None
    #: 链上最后一条 refresh token 的签发时间，是"最后活动"的**近似**：
    #: access token 是无状态的，服务端看不到它被用的时刻。字段名不叫
    #: last_used_at 正是为了不让人把它当成真实的最近调用时间。
    last_rotated_at: datetime | None = None
    resource: str = ""
    revoked_at: datetime | None = None


class ClientOut(BaseModel):
    client_id: str
    client_name: str
    registration_source: str
    #: 本租户下该客户端的安装实例数（含已撤销的）。
    installations: int
    #: 未撤销的实例数。管理者真正关心的是这个 —— 它回答"断开它会影响多少人"。
    active_installations: int
    last_seen_at: datetime | None = None


class RevokeOut(BaseModel):
    family_id: str | None = None
    client_id: str | None = None
    revoked_families: int = 0
    message: str = ""


@asynccontextmanager
async def db_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """带租户上下文的会话。

    单独抽出来是因为本文件有六处要用它，而"忘了设 ``info['tenant_id']``"的后果是
    RLS **静默**返回零行 —— 管理员会看到一个空列表并以为"没有安装实例"，
    而不是"我看不到"。这类失败不报错，只会被当成结论。
    """
    factory = get_session_factory()
    async with factory() as session:
        session.info["tenant_id"] = tenant_id
        yield session


def _clients_query(client_ids: list[str]) -> Select[tuple[OAuthClient]]:
    """按 client_id 取客户端展示信息。

    空集合要**整个跳过**而不是退化成 ``where(False)``：``in_([])`` 生成的语句恒假，
    能跑但语义隐晦。没有已知客户端时就不查这张表 —— 本来也没有名字可显示。
    """
    statement = select(OAuthClient)
    if client_ids:
        statement = statement.where(OAuthClient.client_id.in_(client_ids))
    return statement


async def _family_exists(tenant_id: str, family_id: str) -> bool:
    """本租户是否存在这条轮换链。**带租户条件** —— 跨租户的 id 一律当作不存在。

    不是"查到了再判断租户"：那是先取数据再决定要不要用，泄漏与否取决于调用点有
    没有记得判断。条件直接进 WHERE，让 RLS 与查询条件一起生效。
    """
    async with db_session(tenant_id) as session:
        found = (
            await session.execute(
                select(OAuthToken.jti).where(
                    OAuthToken.tenant_id == tenant_id,
                    OAuthToken.family_id == family_id,
                )
            )
        ).first()
    return found is not None


@router.get("/installations")
@require_auth
@require_capability("mcp_admin")
async def list_installations(request: Request) -> list[InstallationOut]:
    """列出本租户的外部 Agent 安装实例。"""
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        grouped = (
            await session.execute(
                select(
                    OAuthToken.family_id,
                    OAuthToken.client_id,
                    OAuthToken.user_id,
                    OAuthToken.role,
                    OAuthToken.resource,
                    func.min(OAuthToken.created_at),
                    func.max(OAuthToken.created_at),
                )
                .where(OAuthToken.tenant_id == tenant_id)
                .group_by(
                    OAuthToken.family_id,
                    OAuthToken.client_id,
                    OAuthToken.user_id,
                    OAuthToken.role,
                    OAuthToken.resource,
                )
                .order_by(func.max(OAuthToken.created_at).desc())
            )
        ).all()

        # 未撤销条数要单独查：把 revoked_at 放进 group_by 会把一条链拆成两行。
        active_counts: dict[str, int] = {}
        for family_id, count in (
            await session.execute(
                select(OAuthToken.family_id, func.count())
                .where(OAuthToken.tenant_id == tenant_id, OAuthToken.revoked_at.is_(None))
                .group_by(OAuthToken.family_id)
            )
        ).all():
            active_counts[str(family_id)] = int(count)

        revoked_at_by_family: dict[str, datetime] = {}
        for family_id, revoked_at in (
            await session.execute(
                select(OAuthToken.family_id, func.min(OAuthToken.revoked_at))
                .where(OAuthToken.tenant_id == tenant_id, OAuthToken.revoked_at.is_not(None))
                .group_by(OAuthToken.family_id)
            )
        ).all():
            if revoked_at is not None:
                revoked_at_by_family[str(family_id)] = revoked_at

    return [
        InstallationOut(
            family_id=str(family_id),
            client_id=str(client_id),
            user_id=str(user_id),
            role=str(role),
            active_refresh_tokens=active_counts.get(str(family_id), 0),
            created_at=created_at,
            last_rotated_at=last_rotated_at,
            resource=str(resource or ""),
            revoked_at=revoked_at_by_family.get(str(family_id)),
        )
        for family_id, client_id, user_id, role, resource, created_at, last_rotated_at in grouped
    ]


@router.post("/installations/{family_id}/revoke")
@require_auth
@require_capability("mcp_admin")
async def revoke_installation(request: Request, family_id: str) -> RevokeOut:
    """撤销一个安装实例：整条 refresh 轮换链立即失效。

    先确认本租户**确实有**这个安装实例，再撤销。不做这步的话，撤销一个不存在的
    或属于别的租户的实例都会返回"成功" —— 管理员会以为处置生效了，而实际上什么
    都没发生。这是最糟的一类反馈：错的不是结果，是结论。
    """
    tenant_id = require_tenant_id(request)
    if not await _family_exists(tenant_id, family_id):
        raise NotFoundError("oauth_family", family_id)

    await revoke_family_in_own_session(family_id, reason=REVOKED_REASON_USER_REVOKED, tenant_id=tenant_id)

    async with db_session(tenant_id) as session:
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_installation_revoked",
            object_type="oauth_family",
            object_id=family_id,
            details={"revoked_at": datetime.now(UTC).isoformat()},
        )
        await session.commit()

    return RevokeOut(family_id=family_id, revoked_families=1, message="该安装实例已撤销")


@router.get("/clients")
@require_auth
@require_capability("mcp_admin")
async def list_clients(request: Request) -> list[ClientOut]:
    """列出本租户见过的客户端及其影响面。"""
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        totals: dict[str, int] = {}
        for client_id, count in (
            await session.execute(
                select(OAuthToken.client_id, func.count(func.distinct(OAuthToken.family_id)))
                .where(OAuthToken.tenant_id == tenant_id)
                .group_by(OAuthToken.client_id)
            )
        ).all():
            totals[str(client_id)] = int(count)

        actives: dict[str, int] = {}
        for client_id, count in (
            await session.execute(
                select(OAuthToken.client_id, func.count(func.distinct(OAuthToken.family_id)))
                .where(OAuthToken.tenant_id == tenant_id, OAuthToken.revoked_at.is_(None))
                .group_by(OAuthToken.client_id)
            )
        ).all():
            actives[str(client_id)] = int(count)

        last_seen: dict[str, datetime] = {}
        for client_id, seen_at in (
            await session.execute(
                select(OAuthToken.client_id, func.max(OAuthToken.created_at))
                .where(OAuthToken.tenant_id == tenant_id)
                .group_by(OAuthToken.client_id)
            )
        ).all():
            if seen_at is not None:
                last_seen[str(client_id)] = seen_at

        # OAuthClient 是 AS 的部署级对象（无租户列），所以按 client_id 过滤而不是
        # 按租户 —— 这里只是取展示用的名称，取不到就退回用 id 当名字。
        known = {row.client_id: row for row in (await session.execute(_clients_query(list(totals)))).scalars().all()}

    return [
        ClientOut(
            client_id=client_id,
            client_name=known[client_id].client_name if client_id in known else client_id,
            registration_source=known[client_id].registration_source if client_id in known else "unknown",
            installations=totals[client_id],
            active_installations=actives.get(client_id, 0),
            last_seen_at=last_seen.get(client_id),
        )
        for client_id in sorted(totals)
    ]


@router.post("/clients/{client_id:path}/revoke")
@require_auth
@require_capability("mcp_admin")
async def revoke_client(request: Request, client_id: str) -> RevokeOut:
    """撤销某个客户端在**本租户**下的所有安装实例。

    ``client_id`` 是 AS 的部署级对象，同一个客户端可能被多个租户授权。不限定租户
    的话，一个租户的管理员能断掉别的租户的接入 —— 那是一个跨租户的破坏面，而且
    看起来还像"正常工作"。CIMD 客户端的 id 是一个 HTTPS URL（含斜杠），所以路由
    参数用 ``:path`` 而不是默认的 ``:str``。
    """
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        families = [
            str(row)
            for row in (
                await session.execute(
                    select(func.distinct(OAuthToken.family_id)).where(
                        OAuthToken.tenant_id == tenant_id,
                        OAuthToken.client_id == client_id,
                        OAuthToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        ]

    if not families:
        raise NotFoundError("oauth_client_installations", client_id)

    for family_id in families:
        await revoke_family_in_own_session(family_id, reason=REVOKED_REASON_USER_REVOKED, tenant_id=tenant_id)

    async with db_session(tenant_id) as session:
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_client_revoked",
            object_type="oauth_client",
            object_id=client_id,
            details={"revoked_families": len(families), "revoked_at": datetime.now(UTC).isoformat()},
        )
        await session.commit()

    return RevokeOut(
        client_id=client_id,
        revoked_families=len(families),
        message=f"已撤销该客户端在本租户下的 {len(families)} 个安装实例",
    )
