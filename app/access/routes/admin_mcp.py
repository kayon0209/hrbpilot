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
from app.data.models.oauth import OAuthClient, OAuthClientBlock, OAuthToken
from app.mcp.installations import InstallationOut
from app.mcp.installations import list_installations as query_installations
from app.oauth.tokens import REVOKED_REASON_USER_REVOKED, revoke_family
from app.shared.audit import append_security_audit_event
from app.shared.errors import NotFoundError

router = APIRouter(prefix="/api/admin/mcp", tags=["admin-mcp"])


class ClientOut(BaseModel):
    client_id: str
    client_name: str
    registration_source: str
    #: 本租户下该客户端的安装实例数（含已撤销的）。
    installations: int
    #: 未撤销的实例数。管理者真正关心的是这个 —— 它回答"断开它会影响多少人"。
    active_installations: int
    last_seen_at: datetime | None = None
    blocked: bool = False


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


@router.get("/installations")
@require_auth
@require_capability("mcp_admin")
async def list_installations(request: Request) -> list[InstallationOut]:
    """列出本租户的外部 Agent 安装实例。"""
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        # 查询只写一份（app/mcp/installations.py）：管理端与用户自助端读的是同一个
        # 事实，两份 SQL 迟早漂移成"管理端说还有一个、用户端说没有了"。
        return await query_installations(session, tenant_id)


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
    async with db_session(tenant_id) as session:
        found = (
            await session.execute(
                select(OAuthToken.jti).where(
                    OAuthToken.tenant_id == tenant_id,
                    OAuthToken.family_id == family_id,
                )
            )
        ).first()
        if found is None:
            raise NotFoundError("oauth_family", family_id)
        now = datetime.now(UTC)
        await revoke_family(
            session,
            family_id,
            reason=REVOKED_REASON_USER_REVOKED,
            tenant_id=tenant_id,
            now=now,
        )
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_installation_revoked",
            object_type="oauth_family",
            object_id=family_id,
            details={"revoked_at": now.isoformat()},
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
        blocked_ids = set(
            (
                await session.execute(
                    select(OAuthClientBlock.client_id).where(
                        OAuthClientBlock.tenant_id == tenant_id,
                        OAuthClientBlock.blocked.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

    return [
        ClientOut(
            client_id=client_id,
            client_name=known[client_id].client_name if client_id in known else client_id,
            registration_source=known[client_id].registration_source if client_id in known else "unknown",
            installations=totals[client_id],
            active_installations=actives.get(client_id, 0),
            last_seen_at=last_seen.get(client_id),
            blocked=client_id in blocked_ids,
        )
        for client_id in sorted(totals)
    ]


@router.post("/revoke-all")
@require_auth
@require_capability("mcp_admin")
async def revoke_all(request: Request) -> RevokeOut:
    """撤销本租户下**所有**外部 Agent 的授权。

    这是"我们怀疑有人拿到了凭据，先把门关上"用的那个入口。它必须存在且必须便宜：
    应急处置时要求管理员先列出客户端再逐个点，等于把响应时间绑在界面操作上。

    幂等：没有活跃安装实例时返回 0 而不是报错 —— 紧急按钮不该因为"已经没有可撤销的
    东西"而失败。
    """
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        families = [
            str(row)
            for row in (
                await session.execute(
                    select(func.distinct(OAuthToken.family_id)).where(
                        OAuthToken.tenant_id == tenant_id,
                        OAuthToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        ]

        now = datetime.now(UTC)
        for family_id in families:
            await revoke_family(
                session,
                family_id,
                reason=REVOKED_REASON_USER_REVOKED,
                tenant_id=tenant_id,
                now=now,
            )
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_all_installations_revoked",
            object_type="oauth_tenant",
            object_id=tenant_id,
            details={"revoked_families": len(families), "revoked_at": now.isoformat()},
        )
        await session.commit()

    return RevokeOut(
        revoked_families=len(families),
        message=f"已撤销本租户下的全部 {len(families)} 个外部 Agent 授权",
    )


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

        known_client = await session.get(OAuthClient, client_id)
        belongs_to_tenant = known_client is not None and known_client.tenant_id == tenant_id
        if not families and not belongs_to_tenant:
            raise NotFoundError("oauth_client_installations", client_id)
        block = await session.get(OAuthClientBlock, (tenant_id, client_id))
        if block is None:
            session.add(
                OAuthClientBlock(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    blocked=True,
                    reason="tenant administrator revoked client",
                    blocked_by=str(getattr(request.state, "user_id", "") or ""),
                )
            )
        else:
            block.blocked = True
            block.reason = "tenant administrator revoked client"
            block.blocked_by = str(getattr(request.state, "user_id", "") or "")
        now = datetime.now(UTC)
        for family_id in families:
            await revoke_family(
                session,
                family_id,
                reason=REVOKED_REASON_USER_REVOKED,
                tenant_id=tenant_id,
                now=now,
            )
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_client_revoked",
            object_type="oauth_client",
            object_id=client_id,
            details={"revoked_families": len(families), "revoked_at": now.isoformat()},
        )
        await session.commit()

    return RevokeOut(
        client_id=client_id,
        revoked_families=len(families),
        message=f"已撤销该客户端在本租户下的 {len(families)} 个安装实例",
    )


@router.post("/clients/{client_id:path}/enable")
@require_auth
@require_capability("mcp_admin")
async def enable_client(request: Request, client_id: str) -> RevokeOut:
    """Remove the tenant-local deny state; existing token families stay revoked."""
    tenant_id = require_tenant_id(request)
    async with db_session(tenant_id) as session:
        block = await session.get(OAuthClientBlock, (tenant_id, client_id))
        if block is None or not block.blocked:
            raise NotFoundError("blocked_oauth_client", client_id)
        block.blocked = False
        block.reason = ""
        block.blocked_by = str(getattr(request.state, "user_id", "") or "")
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=str(getattr(request.state, "user_id", "") or ""),
            action="mcp_client_enabled",
            object_type="oauth_client",
            object_id=client_id,
            details={"enabled_at": datetime.now(UTC).isoformat()},
        )
        await session.commit()
    return RevokeOut(client_id=client_id, message="该客户端已允许重新授权；旧令牌仍然失效")
