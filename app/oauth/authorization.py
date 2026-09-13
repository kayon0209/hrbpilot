"""授权码的签发与兑换，以及 PKCE 校验。

PKCE 在这里不是可选项
---------------------
本 AS 只签发 public client（没有 client secret，见 ``clients.py``），所以 PKCE 是
**唯一**把"换令牌的人"与"发起授权的人"绑定起来的机制。授权码会出现在重定向 URL、
浏览器历史、Referer 与客户端日志里 —— 没有 PKCE，任何看到过那个 URL 的人都能拿它
换到可读员工数据的令牌。

只接受 ``S256``
---------------
``plain`` 被明确拒绝：它把 verifier 原样送进授权请求，于是"证明持有 verifier"
退化成"证明看过授权 URL"，等于把 PKCE 的作用整个抹掉。RFC 7636 §4.2 允许服务端只
支持 S256，本服务就是这么做的。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, update

from app.config.settings import settings
from app.data.models.oauth import OAuthAuthorizationCode
from app.oauth.storage import (
    constant_time_equals,
    hash_opaque_value,
    new_opaque_value,
    oauth_session,
)

#: PKCE 唯一接受的方法。
SUPPORTED_CODE_CHALLENGE_METHODS = ("S256",)

#: RFC 7636 §4.1 规定的 verifier 长度区间。
_MIN_VERIFIER_LENGTH = 43
_MAX_VERIFIER_LENGTH = 128


class AuthorizationCodeError(Exception):
    """兑换授权码失败。``error`` 用 RFC 6749 §5.2 的错误码。"""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


def code_challenge_for(code_verifier: str) -> str:
    """RFC 7636 §4.2 的 S256 变换：``BASE64URL(SHA256(ASCII(verifier)))``。"""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(*, code_verifier: str, code_challenge: str) -> bool:
    """校验 verifier 是否匹配当初登记的 challenge。

    先看长度：RFC 7636 §4.1 规定了 43–128 字节的区间，越界直接不合法。真正的比较用
    定长比较 —— verifier 是攻击者可控输入，普通 ``==`` 会泄漏"前缀匹配了多少个字符"
    的时间差。
    """
    if not (_MIN_VERIFIER_LENGTH <= len(code_verifier) <= _MAX_VERIFIER_LENGTH):
        return False
    return constant_time_equals(code_challenge_for(code_verifier), code_challenge)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(moment: datetime.datetime) -> datetime.datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC)


@dataclass(frozen=True)
class AuthorizationCodeRecord:
    """一次成功兑换的结果 —— 换令牌所需的全部信息。"""

    client_id: str
    tenant_id: str
    user_id: str
    role: str
    email: str
    redirect_uri: str
    scope: str
    resource: str


async def issue_authorization_code(
    *,
    client_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    email: str,
    redirect_uri: str,
    scope: str,
    resource: str,
    code_challenge: str,
) -> str:
    """登记一个授权码，返回**明文**（只在这一刻存在，库里只有它的 SHA-256）。"""
    raw_code = new_opaque_value()
    row = OAuthAuthorizationCode(
        id=str(uuid4()),
        code_sha256=hash_opaque_value(raw_code),
        client_id=client_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        email=email,
        redirect_uri=redirect_uri,
        scope=scope,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method="S256",
        expires_at=_now() + datetime.timedelta(seconds=settings.oauth_authorization_code_ttl_seconds),
    )
    async with oauth_session() as session:
        session.add(row)
    return raw_code


async def redeem_authorization_code(
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> AuthorizationCodeRecord:
    """兑换授权码。任何一项不匹配都以 ``invalid_grant`` 拒绝。

    所有违规共用一个错误码是**有意**的（RFC 6749 §5.2 只定义 ``invalid_grant``）：
    区分"码不存在"、"码过期"、"redirect_uri 不同"会给出一枚可用于探测的 oracle，
    让攻击者能把搜索空间收窄。真正的区分留给服务端日志。
    """
    async with oauth_session() as session:
        result = await session.execute(
            select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.code_sha256 == hash_opaque_value(code))
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AuthorizationCodeError("invalid_grant", "authorization code is unknown")
        if row.consumed_at is not None:
            raise AuthorizationCodeError("invalid_grant", "authorization code has already been used")
        if _as_utc(row.expires_at) <= _now():
            raise AuthorizationCodeError("invalid_grant", "authorization code has expired")
        if row.client_id != client_id:
            raise AuthorizationCodeError("invalid_grant", "authorization code was issued to a different client")
        if row.redirect_uri != redirect_uri:
            raise AuthorizationCodeError("invalid_grant", "redirect_uri does not match the authorization request")
        if not verify_pkce(code_verifier=code_verifier, code_challenge=row.code_challenge):
            raise AuthorizationCodeError("invalid_grant", "PKCE verification failed")

        # **原子地**占用这个码：UPDATE 带 ``consumed_at IS NULL`` 条件，因此并发的
        # 两个兑换请求里只有一个能改动到行。先 SELECT 再无条件 UPDATE 会留下一个
        # 竞态窗口，而那个窗口正好就是"同一个码换出两份令牌"。
        claimed = await session.execute(
            update(OAuthAuthorizationCode)
            .where(
                OAuthAuthorizationCode.id == row.id,
                OAuthAuthorizationCode.consumed_at.is_(None),
            )
            .values(consumed_at=_now())
        )
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            raise AuthorizationCodeError("invalid_grant", "authorization code has already been used")

        return AuthorizationCodeRecord(
            client_id=row.client_id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            role=row.role,
            email=row.email,
            redirect_uri=row.redirect_uri,
            scope=row.scope,
            resource=row.resource,
        )
