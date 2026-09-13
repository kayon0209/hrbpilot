"""凭据 → ``McpPrincipal`` 的适配层 —— 把"不同来源的已验证身份"归一。

两条出口拿到的凭据形态不同：

- MCP 协议出口 ``/mcp`` 是 Starlette 挂载的子应用，**不经过** Auth 中间件，
  只能自己从 ``Authorization`` 头解令牌；
- REST 桥 ``/api/mcp`` 经过 ``AuthMiddleware``，身份已经在 ``request.scope["auth"]``
  里（中间件已经用同一套 settings 验过签名）。

两者都必须产出**同一种** ``McpPrincipal`` —— 否则授权函数无法共用，两条出口就会
再次漂移（WP1 修掉的正是这个：``/mcp`` 的读工具不校验角色，而 ``/api/mcp`` 校验）。

所以：解码规则只有一份（``app.access.tokens``），主体构造只有一个构造函数
（``verified_principal``），两条出口只是取凭据的位置不同。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.access.scopes import scopes_for_role
from app.access.tokens import AccessClaims, TokenRejection, bearer_token, read_access_claims
from app.mcp.auth.principal import INTERNAL_CLIENT_ID, AuthMethod, McpPrincipal


def verified_principal(
    *,
    user_id: str,
    role: str,
    tenant_id: str,
    token_id: str | None = None,
) -> McpPrincipal:
    """用**已经验证过**的三个身份字段构造平台自签主体。

    只接受这三个字段是刻意的：tenant/role 只能来自已验证凭据，不接受工具参数、
    query 或 header 覆盖。调用方若拿到的是未验证的输入，那是调用方的缺陷，
    本函数不会替它判断。

    scope 由角色能力投影得到（``app.access.scopes.scopes_for_role``），
    客户端上限为 ``None`` —— 平台自签凭据不属于任何客户端安装实例。
    """
    return McpPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        auth_method=AuthMethod.INTERNAL,
        client_id=INTERNAL_CLIENT_ID,
        installation_id=None,
        scopes=scopes_for_role(role),
        client_ceiling=None,
        credential_id=None,
        token_id=token_id,
    )


def principal_from_claims(claims: AccessClaims) -> McpPrincipal:
    """已校验的 access token 载荷 → 主体。"""
    return verified_principal(
        user_id=claims.user_id,
        role=claims.role,
        tenant_id=claims.tenant_id,
        token_id=claims.token_id,
    )


def principal_from_bearer_token(token: str | None) -> McpPrincipal | None:
    """令牌 → 主体；令牌不可用时返回 ``None``。

    返回 ``None`` 在这条链路上表示**匿名**，而不是"错误"：MCP 出口要把匿名读
    当作 ``AUTH_REQUIRED`` 正常终态返回，而不是抛异常。
    """
    if not token:
        return None
    claims = read_access_claims(token)
    if isinstance(claims, TokenRejection):
        return None
    return principal_from_claims(claims)


def principal_from_authorization_header(header_value: str | None) -> McpPrincipal | None:
    """``Authorization`` 头 → 主体。"""
    return principal_from_bearer_token(bearer_token(header_value))


def principal_from_headers(headers: Mapping[str, str] | None) -> McpPrincipal | None:
    """HTTP 头 → 主体。头名按小写匹配。

    不依赖 ``Mapping`` 的键大小写（不同客户端发 ``Authorization`` / ``authorization``
    的都有），因此先归一再取 —— 这也修掉了原先"两次 ``.get`` 只覆盖两种拼法"的写法。
    """
    if not headers:
        return None
    normalized = {str(key).lower(): value for key, value in headers.items()}
    return principal_from_authorization_header(normalized.get("authorization"))
