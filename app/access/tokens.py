"""访问令牌解码与 claim 校验的**唯一入口**。

为什么需要它
------------
同一个 access token 的校验规则原先写了两遍：``app/access/middleware/auth.py``
（HTTP 中间件）和 ``app/mcp/server.py``（MCP 工具出口）各自 ``jwt.decode(...)``
一次，再各自校验 ``type`` / ``sub`` / ``role`` / ``tenant_id``。

这在那条边界上加 OAuth、audience 与 scope 之前只是轻微重复；之后它会变成
**静默越权**：中间件和后端出口只要有一处改了校验条件，就会出现"一个放行、
另一个也放行，但放行条件不同"的状态，而两边都自认为正确。所以先把解码与
claim 校验收敛成一次。

职责边界：本模块只回答"这个令牌说明了什么身份"。**怎么向调用方表达拒绝**
不在这里 —— HTTP 中间件要返回带具体文案的 401，MCP 出口则把匿名当作"未认证
但不是错误"（见 ``app/mcp/contract`` 的 AUTH_REQUIRED 语义）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from jose import JWTError, jwt

from app.config.settings import settings

_BEARER_PREFIX = "Bearer "

#: 平台自签凭据在 ``request.scope["auth"]`` 里的来源标记。
#:
#: 为什么要显式标记：REST 桥只能从 claim 重建"平台自签"主体（无客户端、无授权
#: 上限）。将来引入 OAuth 时，如果这里继续把外部令牌也当自签凭据，
#: **客户端授权上限会被静默丢掉** —— 这条桥会重新变成最弱的一环。标记让
#: "这个凭据是不是自签的"成为一个显式事实，REST 桥据此 fail-closed。
#:
#: 取值必须与 ``app.mcp.auth.principal.AuthMethod.INTERNAL`` 一致
#: （有测试断言）。定义在 access 层是为了让中间件不依赖 mcp 层。
SCOPE_AUTH_METHOD_INTERNAL = "internal"


class TokenRejection(StrEnum):
    """凭据被拒的类别。刻意只区分**调用方需要区别对待**的几种。"""

    MALFORMED = "malformed"  # 签名 / 过期 / audience / issuer 不通过，或根本不是 JWT
    WRONG_TYPE = "wrong_type"  # 是有效令牌，但不是 access token（例如 refresh token）
    INCOMPLETE = "incomplete"  # 缺 sub / role / tenant_id —— 无法确定作用范围


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """已校验的 access token 载荷。字段都是规范化过的字符串。"""

    user_id: str
    role: str
    tenant_id: str
    email: str
    token_id: str | None


def read_access_claims(token: str) -> AccessClaims | TokenRejection:
    """解码并校验一个 access token；失败返回 ``TokenRejection``，不抛异常。

    调用方必须显式处理返回值 —— 返回类型不是 ``Optional`` 而是联合类型，
    就是为了让"忘了处理拒绝"变成类型检查期可见的问题。
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError:
        return TokenRejection.MALFORMED
    except (TypeError, ValueError):
        # python-jose 对某些畸形输入（例如非 ASCII 的粘贴错误）会抛这两类，
        # 而不是 JWTError。它们同样是"这个令牌不可用"，不该变成 500。
        return TokenRejection.MALFORMED

    if payload.get("type") != "access":
        return TokenRejection.WRONG_TYPE

    user_id = payload.get("sub")
    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    if not all(isinstance(value, str) and value for value in (user_id, role, tenant_id)):
        return TokenRejection.INCOMPLETE

    token_id = payload.get("jti")
    return AccessClaims(
        user_id=str(user_id),
        role=str(role),
        tenant_id=str(tenant_id),
        email=str(payload.get("email") or ""),
        token_id=str(token_id) if isinstance(token_id, str) and token_id else None,
    )


def bearer_token(header_value: str | None) -> str | None:
    """从 ``Authorization`` 头里取出令牌；不是 Bearer 形态时返回 None。"""
    if not header_value or not header_value.startswith(_BEARER_PREFIX):
        return None
    token = header_value[len(_BEARER_PREFIX) :].strip()
    return token or None
