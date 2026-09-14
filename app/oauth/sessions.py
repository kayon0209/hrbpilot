"""AS 自己的浏览器登录会话。

为什么 AS 需要一份自己的会话
---------------------------
授权端点要回答"现在是谁在授权"。平台的网页登录用的是 localStorage 里的 Bearer
令牌 —— 那个东西不会随着跨站跳转发到 AS 这个**独立进程**上；而把平台令牌直接塞进
cookie，又等于把一个能调 ``/api/*`` 的凭据暴露在 CSRF 面上。

所以 AS 维护一份**用途单一**的会话 cookie：只用于回答"这次授权是谁点的同意"，
不能用来调任何业务接口。

密钥做域分离
------------
签名用的不是 ``JWT_SECRET`` 原文，而是由它派生出的一个专用密钥。同一个密钥服务两种
令牌（平台会话 + AS 会话）即使当前靠 ``type`` 字段能互相拒绝，也仍然是"两个系统的
安全边界压在一个值上"。派生一次，代价是零，边界就分开了。

``SameSite=Lax`` 而不是 ``Strict``：授权流程是**从站外跳进来**的（用户点"连接到
HRBPilot"），``Strict`` 会让 AS 在这一跳里看不到自己的 cookie，于是每个用户每次授权
都要重新登录一次。
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
from dataclasses import dataclass

from jose import JWTError
from jose import jwt as jose_jwt

from app.config.settings import settings

SESSION_COOKIE_NAME = "hrbp_as_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
_TOKEN_TYPE = "as_session"


@dataclass(frozen=True)
class AsSession:
    """一次已登录的 AS 会话。只承载"是谁"，不承载任何权限判断。"""

    user_id: str
    tenant_id: str
    role: str
    email: str
    name: str
    auth_version: int = 1


def _session_signing_key() -> str:
    """从 ``JWT_SECRET`` 派生 AS 会话专用密钥（见模块说明）。"""
    return hmac.new(settings.jwt_secret.encode("utf-8"), b"hrbp-oauth-as-session-v1", hashlib.sha256).hexdigest()


def issue_session_cookie_value(session: AsSession, *, now: datetime.datetime | None = None) -> str:
    moment = now or datetime.datetime.now(datetime.UTC)
    claims = {
        "sub": session.user_id,
        "tenant_id": session.tenant_id,
        "role": session.role,
        "email": session.email,
        "name": session.name,
        "auth_version": session.auth_version,
        "type": _TOKEN_TYPE,
        "iat": int(moment.timestamp()),
        "exp": int((moment + datetime.timedelta(seconds=SESSION_TTL_SECONDS)).timestamp()),
    }
    return str(jose_jwt.encode(claims, _session_signing_key(), algorithm="HS256"))


def read_session_cookie(value: str | None) -> AsSession | None:
    """解析会话 cookie；任何不合法都返回 ``None``（等价于"未登录"）。"""
    if not value:
        return None
    try:
        claims = jose_jwt.decode(value, _session_signing_key(), algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("type") != _TOKEN_TYPE:
        return None
    user_id = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    if not (isinstance(user_id, str) and user_id) or not (isinstance(tenant_id, str) and tenant_id):
        return None
    if not (isinstance(role, str) and role):
        return None
    return AsSession(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=str(claims.get("email") or ""),
        name=str(claims.get("name") or ""),
        auth_version=int(claims.get("auth_version") or 0),
    )
