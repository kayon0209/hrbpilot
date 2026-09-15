"""外部 Agent 安装实例：查询与自助撤销的单一实现。

为什么单独一个模块
------------------
"租户里有哪些安装实例"是同一个事实，但有两个视图：管理端看全租户
（``/api/admin/mcp``），用户端只看自己（「我接过的助手」）。两个视图各写一份 SQL
迟早会漂移 —— 一处记得排除已撤销、另一处忘了，于是管理端说"还有一个"、用户端说
"没有了"，而两边读的是同一张表。所以查询只写一份，差异用参数表达。

"活跃"的判据
------------
``active_refresh_tokens > 0`` 且未撤销。access token 是无状态 JWT，服务端看不到它
被使用的时刻；能续期的只有 refresh token，所以"还有未撤销的 refresh token"就是
"这个实例还活着"。

自助撤销的边界
--------------
用户能撤销的只有**自己的**实例，因此撤销前必须按 ``user_id`` 校验归属。不校验的
话，任何登录用户拿到别人的 ``family_id`` 就能把对方踢下线，而 ``family_id`` 是
可能从日志、截图或报错信息里泄漏的。归属不符与"不存在"返回**同一个结果**，
不做区分 —— 区分它等于提供一个"这个 id 是否存在"的探测器。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.oauth import OAuthToken
from app.data.models.user import User
from app.oauth.tokens import REVOKED_REASON_USER_REVOKED, revoke_family
from app.shared.audit import append_security_audit_event


class InstallationOut(BaseModel):
    """一个安装实例（= 一条 refresh 轮换链）的最小视图。"""

    family_id: str
    client_id: str
    user_id: str
    #: 租户内用户的显示名。用户记录已被清理时为 ``None``，管理端仍保留不可歧义的 id。
    user_name: str | None = None
    role: str
    #: 未撤销的 refresh token 条数。为 0 表示已经没有可续期的凭据。
    #:
    #: ⚠️ 这**不**意味着"要等 access token 自然过期才彻底失效"：`revoke_family` 会同时
    #: 写一条 family 粒度的撤销记录，RS 侧每次验令牌都查它（``oauth_revocations.is_revoked``），
    #: 所以撤销是**立即**生效的。原注释写成"现有 access token 到期后即彻底失效"，会让人
    #: 以为那条撤销比对是多余的优化，从而删掉它 —— 那才是真正让即时吊销失效的做法。
    active_refresh_tokens: int
    created_at: datetime | None = None
    #: 链上最后一条 refresh token 的签发时间，是"最后活动"的**近似**：
    #: access token 是无状态的，服务端看不到它被用的时刻。字段名不叫
    #: last_used_at 正是为了不让人把它当成真实的最近调用时间。
    last_rotated_at: datetime | None = None
    resource: str = ""
    revoked_at: datetime | None = None
    #: 该实例声明的 scope 清单（去重后）。用户自助视图据此显示"仅查询/可提交办理"。
    scopes: list[str] = []


async def list_installations(
    session: AsyncSession,
    tenant_id: str,
    *,
    user_id: str | None = None,
    active_only: bool = False,
) -> list[InstallationOut]:
    """列出安装实例。``user_id`` 给定时只返回该用户的；``active_only`` 只留未撤销且仍可续期的。"""
    conditions = [OAuthToken.tenant_id == tenant_id]
    if user_id is not None:
        conditions.append(OAuthToken.user_id == user_id)

    grouped = (
        await session.execute(
            select(
                OAuthToken.family_id,
                OAuthToken.client_id,
                OAuthToken.user_id,
                User.name.label("user_name"),
                OAuthToken.role,
                OAuthToken.resource,
                func.min(OAuthToken.created_at),
                func.max(OAuthToken.created_at),
            )
            .outerjoin(
                User,
                (User.id == OAuthToken.user_id) & (User.tenant_id == OAuthToken.tenant_id),
            )
            .where(*conditions)
            .group_by(
                OAuthToken.family_id,
                OAuthToken.client_id,
                OAuthToken.user_id,
                User.name,
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
            .where(*conditions, OAuthToken.revoked_at.is_(None))
            .group_by(OAuthToken.family_id)
        )
    ).all():
        active_counts[str(family_id)] = int(count)

    revoked_at_by_family: dict[str, datetime] = {}
    for family_id, revoked_at in (
        await session.execute(
            select(OAuthToken.family_id, func.min(OAuthToken.revoked_at))
            .where(*conditions, OAuthToken.revoked_at.is_not(None))
            .group_by(OAuthToken.family_id)
        )
    ).all():
        if revoked_at is not None:
            revoked_at_by_family[str(family_id)] = revoked_at

    # scope 也按 family 聚合：一条链上各次轮换声明的 scope 可能不同（用户改过套餐），
    # 取并集才能回答"这个实例现在一共能做什么"。
    scopes_by_family: dict[str, set[str]] = {}
    for family_id, scope in (
        await session.execute(
            select(OAuthToken.family_id, OAuthToken.scope)
            .where(*conditions, OAuthToken.revoked_at.is_(None))
            .distinct()
        )
    ).all():
        bucket = scopes_by_family.setdefault(str(family_id), set())
        for item in str(scope or "").split():
            if item:
                bucket.add(item)

    records = [
        InstallationOut(
            family_id=str(family_id),
            client_id=str(client_id),
            user_id=str(owner_id),
            user_name=str(user_name) if user_name else None,
            role=str(role),
            active_refresh_tokens=active_counts.get(str(family_id), 0),
            created_at=created_at,
            last_rotated_at=last_rotated_at,
            resource=str(resource or ""),
            revoked_at=revoked_at_by_family.get(str(family_id)),
            scopes=sorted(scopes_by_family.get(str(family_id), set())),
        )
        for family_id, client_id, owner_id, user_name, role, resource, created_at, last_rotated_at in grouped
    ]
    if active_only:
        records = [record for record in records if record.revoked_at is None and record.active_refresh_tokens > 0]
    return records


async def revoke_own_installation(
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
    family_id: str,
    *,
    actor_id: str = "",
) -> bool:
    """撤销**自己**的一个安装实例。不属于自己（或不存在）一律返回 ``False``。

    调用方负责 ``commit``，好让撤销与审计事件落在同一个事务里 —— 拆成两个事务的话，
    审计写失败会留下"已撤销但没记录"的静默缺口。
    """
    owned = (
        await session.execute(
            select(OAuthToken.jti).where(
                OAuthToken.tenant_id == tenant_id,
                OAuthToken.user_id == user_id,
                OAuthToken.family_id == family_id,
            )
        )
    ).first()
    if owned is None:
        return False

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
        actor_id=actor_id or user_id,
        action="mcp_installation_revoked",
        object_type="oauth_family",
        object_id=family_id,
        details={"revoked_at": now.isoformat(), "self_service": True},
    )
    return True
