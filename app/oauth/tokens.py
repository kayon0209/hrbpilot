"""访问令牌与刷新令牌的签发、轮换与撤销。

access token 是无状态 JWT，refresh token 是落库的不透明串 —— 这个分工不是随意的：

- **access token 每次请求都要被校验**，所以它必须能被 RS 本地验证（JWKS 验签），
  不能引入一次网络往返或一次数据库查询；
- **refresh token 每次授权只被用几次**，可以承担一次数据库查询，换来的是"能被真正
  撤销"与"能被检测重用"这两种无状态令牌给不了的能力。

刷新令牌轮换与重用检测
----------------------
每次刷新都签发一把**新的** refresh token，并把旧的那把标记为已用。合法客户端永远
持有最新的那一把。因此**再次出现一把已用的令牌**只有两种可能：它被复制了，或者
攻击者截获了旧的那份 —— 两种情况都按"整条链已失陷"处理：撤销整个 family，
把由它派生的所有 access token 一并作废。

内省（RFC 7662）与撤销共用同一份判据
-----------------------------------
``introspect_token`` 回答的是"这把令牌还有效吗"，而这个问题与 RS 每个请求要问的
完全一样。两处都通过 ``app/data/repositories/oauth_revocations.is_revoked`` 查撤销表，
避免"一处查 jti、另一处漏查 family"这类静默失效。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from jose import JWTError
from jose import jwt as jose_jwt
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.data.models.oauth import (
    REVOCATION_KIND_FAMILY,
    REVOCATION_KIND_JTI,
    OAuthRevokedToken,
    OAuthToken,
)
from app.data.repositories.oauth_revocations import is_revoked
from app.oauth.keys import active_signing_key, verification_keys
from app.oauth.storage import hash_opaque_value, new_opaque_value, oauth_session
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: RFC 9068 §2.1 给 JWT 形式的 access token 规定的 ``typ`` 头。
ACCESS_TOKEN_TYPE = "at+jwt"

REVOKED_REASON_USER_REVOKED = "user_revoked"
REVOKED_REASON_REUSE_DETECTED = "refresh_token_reuse_detected"

#: 撤销记录的存活时间比 access token 的 TTL 多这么一点，用来兜住时钟偏差：
#: 早一秒删掉撤销记录，就等于让一把已被撤销的令牌重新生效一小段时间。
_REVOCATION_CLOCK_SKEW_SECONDS = 300


class TokenError(Exception):
    """令牌签发/兑换/撤销失败。``error`` 用 RFC 6749 §5.2 的错误码。"""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(moment: datetime.datetime) -> datetime.datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC)


def access_token_claims(
    *,
    client_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    email: str,
    scope: str,
    resource: str,
    family_id: str,
    jti: str,
    issued_at: datetime.datetime,
) -> dict[str, Any]:
    """构造 access token 的载荷。

    ``aud`` 是发起授权时请求的 resource（RFC 8707）—— RS 会逐字节比对它，因此一个
    为别的资源签发的令牌在这里毫无用处。``family_id`` 是本服务加的：撤销"这次授权
    会话"时，RS 需要凭它把由同一条链派生的所有 access token 一并作废。
    """
    expires_at = issued_at + datetime.timedelta(seconds=settings.oauth_access_token_ttl_seconds)
    return {
        "iss": settings.oauth_issuer,
        "aud": resource,
        "sub": user_id,
        "client_id": client_id,
        "scope": scope,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
        "family_id": family_id,
        # RS 侧构造 McpPrincipal 所需的最小身份集合。
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
    }


def _build_tokens(
    *,
    client_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    email: str,
    scope: str,
    resource: str,
    family_id: str,
    now: datetime.datetime,
) -> tuple[str, str, OAuthToken]:
    """返回 ``(access_token, refresh_token_明文, refresh_token_行)``。"""
    access_jti = str(uuid4())
    claims = access_token_claims(
        client_id=client_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        email=email,
        scope=scope,
        resource=resource,
        family_id=family_id,
        jti=access_jti,
        issued_at=now,
    )
    access_token = active_signing_key().sign(claims, token_type=ACCESS_TOKEN_TYPE)

    refresh_raw = new_opaque_value()
    row = OAuthToken(
        jti=str(uuid4()),
        token_sha256=hash_opaque_value(refresh_raw),
        family_id=family_id,
        client_id=client_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        email=email,
        scope=scope,
        resource=resource,
        expires_at=now + datetime.timedelta(days=settings.oauth_refresh_token_ttl_days),
    )
    return access_token, refresh_raw, row


async def issue_tokens_for_authorization(
    *,
    client_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    email: str,
    scope: str,
    resource: str,
) -> IssuedTokens:
    """授权码兑换成功后签发第一对令牌。新的一条轮换链从这里开始。"""
    now = _now()
    family_id = str(uuid4())
    access_token, refresh_raw, row = _build_tokens(
        client_id=client_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        email=email,
        scope=scope,
        resource=resource,
        family_id=family_id,
        now=now,
    )
    async with oauth_session() as session:
        session.add(row)
    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_raw,
        expires_in=settings.oauth_access_token_ttl_seconds,
        scope=scope,
    )


async def _record_revocation(
    session: AsyncSession,
    *,
    kind: str,
    value: str,
    expires_at: datetime.datetime,
    reason: str,
    tenant_id: str = "",
) -> None:
    existing = await session.get(OAuthRevokedToken, (kind, value))
    if existing is not None:
        # 已经记过就只延后失效时间，不覆盖原因 —— 首次撤销的原因才是审计想看的那个。
        if _as_utc(existing.expires_at) < expires_at:
            existing.expires_at = expires_at
        return
    session.add(
        OAuthRevokedToken(
            kind=kind,
            value=value,
            expires_at=expires_at,
            reason=reason,
            tenant_id=tenant_id,
        )
    )


async def revoke_family(
    session: AsyncSession,
    family_id: str,
    *,
    now: datetime.datetime,
    reason: str,
    tenant_id: str = "",
) -> None:
    """撤销一条轮换链：链上所有 refresh token 立即失效，并登记 family 撤销记录。

    登记 family 撤销是给 **RS** 用的：RS 手里只有 access token，它没有 refresh token
    的行可查，只能凭令牌里的 ``family_id`` 去撤销表里比对。
    """
    await session.execute(
        update(OAuthToken)
        .where(OAuthToken.family_id == family_id, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    await _record_revocation(
        session,
        kind=REVOCATION_KIND_FAMILY,
        value=family_id,
        expires_at=now
        + datetime.timedelta(seconds=settings.oauth_access_token_ttl_seconds)
        + datetime.timedelta(seconds=_REVOCATION_CLOCK_SKEW_SECONDS),
        reason=reason,
        tenant_id=tenant_id,
    )


async def revoke_family_in_own_session(
    family_id: str,
    *,
    reason: str,
    tenant_id: str = "",
    now: datetime.datetime | None = None,
) -> None:
    """**独立事务**地撤销一条链。

    为什么必须独立：重用检测的调用点紧跟着就要 ``raise TokenError``，而
    ``oauth_session`` 在异常路径上会 ``rollback`` —— 如果撤销写在与抛错同一个会话里，
    它会被自己触发的那次回滚吃掉，于是"检测到重用就撤销整条链"这条安全承诺**静默失效**。
    实现时确实踩到了这个坑（端到端脚本里表现为"链上的新令牌仍然可用"）。
    把它放进自己的事务，这条保证就不再依赖调用点的异常处理顺序。
    """
    moment = now or _now()
    async with oauth_session() as session:
        await revoke_family(session, family_id, now=moment, reason=reason, tenant_id=tenant_id)


async def rotate_refresh_token(*, refresh_token: str, client_id: str) -> IssuedTokens:
    """用 refresh token 换一对新令牌，并把旧的那把作废。"""
    digest = hash_opaque_value(refresh_token)
    now = _now()

    # 第一阶段：只读地判断这把令牌能不能用。任何失败都在**没有写入**的情况下抛出，
    # 因此不存在"写了又被回滚"的问题。
    #
    # 把需要的字段取成局部变量（而不是一个 dict）：这些值要跨出 `async with` 使用，
    # 而 dict 会退化成 `object`，让后面每一次比较都失去类型检查 —— 这个函数里最要紧的
    # 恰恰是那些比较。
    async with oauth_session() as session:
        result = await session.execute(select(OAuthToken).where(OAuthToken.token_sha256 == digest))
        row = result.scalar_one_or_none()
        if row is None or row.client_id != client_id:
            raise TokenError("invalid_grant", "refresh token is unknown or was issued to a different client")
        jti = row.jti
        family_id = row.family_id
        tenant_id = row.tenant_id
        user_id = row.user_id
        role = row.role
        email = row.email
        scope = row.scope
        resource = row.resource
        expires_at = _as_utc(row.expires_at)
        used_at = row.used_at
        revoked_at = row.revoked_at

    if revoked_at is not None:
        raise TokenError("invalid_grant", "refresh token has been revoked")
    if expires_at <= now:
        raise TokenError("invalid_grant", "refresh token has expired")

    if used_at is not None:
        # 重用：合法客户端永远只用最新那把。见模块头部。
        logger.warning("oauth_refresh_reuse_detected", family_id=family_id, client_id=client_id, tenant_id=tenant_id)
        await revoke_family_in_own_session(
            family_id, reason=REVOKED_REASON_REUSE_DETECTED, tenant_id=tenant_id, now=now
        )
        raise TokenError("invalid_grant", "refresh token has already been used; the session has been revoked")

    # 第二阶段：原子占用 + 签发新的一对。
    access_token = ""
    refresh_raw = ""
    lost_race = False
    claim = update(OAuthToken).where(OAuthToken.jti == jti, OAuthToken.used_at.is_(None)).values(used_at=now)
    async with oauth_session() as session:
        claimed = await session.execute(claim)
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            # 并发的另一个刷新抢到了同一把令牌。这在合法客户端上是不会发生的
            # （它只会用最新那把），因此同样按重用处理。
            lost_race = True
        else:
            access_token, refresh_raw, new_row = _build_tokens(
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=str(user_id),
                role=str(role),
                email=str(email),
                scope=str(scope),
                resource=str(resource),
                family_id=family_id,
                now=now,
            )
            new_row.parent_jti = jti
            session.add(new_row)

    if lost_race:
        logger.warning("oauth_refresh_race_detected", family_id=family_id, client_id=client_id)
        await revoke_family_in_own_session(
            family_id, reason=REVOKED_REASON_REUSE_DETECTED, tenant_id=tenant_id, now=now
        )
        raise TokenError("invalid_grant", "refresh token has already been used; the session has been revoked")

    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_raw,
        expires_in=settings.oauth_access_token_ttl_seconds,
        scope=str(scope),
    )


def verify_access_token(token: str) -> dict[str, Any] | None:
    """**AS 自己**验证一把 access token（撤销端点需要它来确认令牌确实是自己签的）。

    不合法就返回 ``None``，不抛异常：调用方要区分的是"这是我签的令牌吗"，
    而不是"它因为哪个原因不合法"。
    """
    try:
        header = jose_jwt.get_unverified_header(token)
    except JWTError:
        return None
    kid = header.get("kid")
    for key in verification_keys():
        if key.kid != kid:
            continue
        try:
            claims: dict[str, Any] = jose_jwt.decode(
                token,
                key.jwk(),
                algorithms=["ES256"],
                audience=settings.mcp_resource_url,
                issuer=settings.oauth_issuer,
            )
        except JWTError:
            return None
        return claims
    return None


async def revoke_token(*, token: str, client_id: str) -> None:
    """撤销一个令牌（RFC 7009）。

    未知令牌**也返回成功** —— 规范如此要求，且这也避免了把本端点变成一个
    "这个令牌存在吗"的探测器。跨客户端撤销不做：``client_id`` 不匹配时只记日志。
    （public client 的 ``client_id`` 是自述的，这道检查挡不住有心人，但它能挡住
    一个客户端在批量清理时误删掉另一个客户端的会话。）
    """
    digest = hash_opaque_value(token)
    now = _now()
    async with oauth_session() as session:
        result = await session.execute(select(OAuthToken).where(OAuthToken.token_sha256 == digest))
        row = result.scalar_one_or_none()
        if row is not None:
            if row.client_id != client_id:
                logger.warning("oauth_revoke_client_mismatch", client_id=client_id, token_client=row.client_id)
                return
            await revoke_family(
                session,
                row.family_id,
                now=now,
                reason=REVOKED_REASON_USER_REVOKED,
                tenant_id=row.tenant_id,
            )
            return

    claims = verify_access_token(token)
    if claims is None:
        return
    token_client = claims.get("client_id")
    if token_client != client_id:
        logger.warning("oauth_revoke_client_mismatch", client_id=client_id, token_client=token_client)
        return
    expires_at = datetime.datetime.fromtimestamp(int(claims["exp"]), tz=datetime.UTC)
    jti = str(claims.get("jti") or "")
    family_id = str(claims.get("family_id") or "")
    if not jti:
        return
    async with oauth_session() as session:
        await _record_revocation(
            session,
            kind=REVOCATION_KIND_JTI,
            value=jti,
            expires_at=expires_at + datetime.timedelta(seconds=_REVOCATION_CLOCK_SKEW_SECONDS),
            reason=REVOKED_REASON_USER_REVOKED,
            tenant_id=str(claims.get("tenant_id") or ""),
        )
        if family_id:
            # 撤销一把 access token 时把整条链一起撤：客户端通常只送当前这把，
            # 但用户按下"撤销授权"的意图显然是整次授权，不是某一个 15 分钟的窗口。
            await revoke_family(
                session,
                family_id,
                now=now,
                reason=REVOKED_REASON_USER_REVOKED,
                tenant_id=str(claims.get("tenant_id") or ""),
            )


async def introspect_token(*, token: str, client_id: str) -> dict[str, Any]:
    """RFC 7662 令牌内省。返回可直接作为响应体的字典。

    两个刻意的取舍
    --------------
    1. **不是本客户端签发的令牌一律回 ``active: false``**，而不是报错。内省端点的
       调用方只需要"能不能用"这一个答案；把"这不是你的令牌"与"这把令牌已过期"分成
       两种响应，等于给了一个探测"某个令牌属于谁"的通道。
    2. **不返回 ``username`` / ``email``**。内省响应会被客户端写进日志，而 RFC 7662
       的这两个字段是可选成员。``sub`` 保留：它就在令牌自身里，对持有者不是新信息，
       而排障时"这是谁"是第一个要回答的问题。
    """
    now = _now()
    digest = hash_opaque_value(token)

    async with oauth_session() as session:
        result = await session.execute(select(OAuthToken).where(OAuthToken.token_sha256 == digest))
        row = result.scalar_one_or_none()
        if row is not None:
            if row.client_id != client_id:
                logger.warning("oauth_introspect_client_mismatch", client_id=client_id, token_client=row.client_id)
                return {"active": False}
            # refresh token 的"有效"有三个条件，缺一个都可能让一把已被换掉的令牌
            # 看起来仍然可用：未撤销、未用过（用过即说明它已被轮换掉）、未过期。
            if row.revoked_at is not None or row.used_at is not None or _as_utc(row.expires_at) <= now:
                return {"active": False}
            return {
                "active": True,
                "client_id": row.client_id,
                "scope": row.scope,
                "sub": row.user_id,
                "aud": row.resource,
                "iss": settings.oauth_issuer,
                "jti": row.jti,
                "iat": int(_as_utc(row.created_at).timestamp()),
                "exp": int(_as_utc(row.expires_at).timestamp()),
                # 自述类型。RFC 7662 的 token_type 指 OAuth 2.0 §5.1 的 "Bearer"，
                # 那个取值对 access token 才成立；这里用更具信息量的取值，并**不**
                # 冒充 Bearer —— 拿 refresh token 去调受保护资源本来就不该被当成正常用法。
                "token_type": "refresh_token",
            }

    claims = verify_access_token(token)
    if claims is None:
        return {"active": False}
    if claims.get("client_id") != client_id:
        logger.warning("oauth_introspect_client_mismatch", client_id=client_id, token_client=claims.get("client_id"))
        return {"active": False}

    jti = str(claims.get("jti") or "")
    family_id = str(claims.get("family_id") or "")
    async with oauth_session() as session:
        if await is_revoked(session, jti=jti, family_id=family_id):
            return {"active": False}

    body: dict[str, Any] = {
        "active": True,
        "client_id": str(claims.get("client_id") or ""),
        "scope": str(claims.get("scope") or ""),
        "sub": str(claims.get("sub") or ""),
        "aud": claims.get("aud"),
        "iss": str(claims.get("iss") or ""),
        "token_type": "Bearer",
    }
    for name in ("jti", "iat", "exp"):
        value = claims.get(name)
        if value is not None:
            body[name] = value
    return body


async def prune_expired_revocations(*, now: datetime.datetime | None = None) -> int:
    """清理已过期的撤销记录。返回删除的行数。

    撤销记录的 ``expires_at`` 是**被撤销令牌自身的过期时间** —— 过了那一刻，
    令牌本来就无效了，这行记录再无意义。于是这张表的大小与"当前在途令牌数"
    成正比，而不是与历史撤销总数成正比。供运维定时任务调用。
    """
    moment = now or _now()
    async with oauth_session() as session:
        result = await session.execute(delete(OAuthRevokedToken).where(OAuthRevokedToken.expires_at < moment))
        return int(getattr(result, "rowcount", 0) or 0)
