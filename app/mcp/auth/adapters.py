"""凭据 → ``McpPrincipal`` 的适配层 —— 把"不同来源的已验证身份"归一。

两条出口拿到的凭据形态不同：

- MCP 协议出口 ``/mcp`` 是 Starlette 挂载的子应用。``AuthMiddleware`` 会先在**传输层**
  处理它（``_dispatch_mcp``：无凭据或凭据无效 → 401 + RFC 9728 挑战），但子应用**拿不到**
  中间件写在 ``request.scope["auth"]`` 里的结果 —— 那是给同层的 RBAC 用的。所以这条出口
  必须自己从 ``Authorization`` 头再解一次令牌；
- REST 桥 ``/api/mcp`` 经过 ``AuthMiddleware``，身份已经在 ``request.scope["auth"]``
  里（中间件已经用同一套 settings 验过签名）。

两者都必须产出**同一种** ``McpPrincipal`` —— 否则授权函数无法共用，两条出口就会
再次漂移（WP1 修掉的正是这个：``/mcp`` 的读工具不校验角色，而 ``/api/mcp`` 校验）。

所以：解码规则只有一份（``app.access.as_tokens.resolve_access_claims``），主体构造只有
两个构造函数（``verified_principal`` 与 ``principal_from_as_claims``），两条出口只是取
凭据的位置不同。

为什么这里的解析是 ``async``
----------------------------
AS 签发的令牌**不能只靠本地计算校验**：要取一次 JWKS（HTTP，带缓存），还要查一次
撤销表（数据库）。平台自签令牌完全不需要这些。把两种凭据的解析收进同一个 async
函数，是为了不让"哪种凭据要查库"这件事泄漏到调用点 —— 调用点只有一句话：
``await principal_from_headers(ctx.headers)``。
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.access.as_tokens import AsAccessClaims, resolve_access_claims
from app.access.scopes import scopes_for_role
from app.access.tokens import AccessClaims, TokenRejection, bearer_token
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
    """已校验的平台自签 access token 载荷 → 主体。"""
    return verified_principal(
        user_id=claims.user_id,
        role=claims.role,
        tenant_id=claims.tenant_id,
        token_id=claims.token_id,
    )


def principal_from_as_claims(claims: AsAccessClaims) -> McpPrincipal | None:
    """已校验的 AS access token 载荷 → 外部主体；无法构造时返回 ``None``。

    ``installation_id`` 取令牌里的 ``family_id``。本仓库**没有**独立的 installations
    表，而"安装实例"在语义上要回答的是"这是用户对哪个客户端的哪一次授权" ——
    一条 refresh 轮换链（family）正好就是那个东西：它在轮换中保持不变、唯一标识一次
    授权、并且是可撤销的粒度（撤销一条 family 会让链上所有 access token 一起失效，
    见 ``app/oauth/tokens.py`` 的 ``revoke_family``）。

    刻意**不**伪造一个 UUID：伪造值会让审计把两条不同的授权读成同一次。

    ``client_ceiling`` 来自 RS 自己查到的客户端注册范围，**不取令牌自述** ——
    令牌是被校验的对象，不能同时充当自己的上限依据（详见 ``as_tokens`` 模块头部）。
    """
    try:
        installation_id = UUID(claims.family_id)
    except ValueError:
        # ``as_tokens`` 已经把 family_id 校验成 UUID 形态；这里是纵深防御，
        # 免得将来那条校验被改动后，这里会拿着一个坏值继续走。
        return None
    return McpPrincipal(
        tenant_id=claims.tenant_id,
        user_id=claims.user_id,
        role=claims.role,
        auth_method=AuthMethod.OAUTH,
        client_id=claims.client_id,
        installation_id=installation_id,
        scopes=claims.scopes,
        client_ceiling=claims.client_ceiling,
        credential_id=None,
        token_id=claims.token_id,
    )


async def principal_from_bearer_token(token: str | None) -> McpPrincipal | None:
    """令牌 → 主体；令牌不可用时返回 ``None``。

    返回 ``None`` 在这条链路上表示**匿名**，而不是"错误"：MCP 出口要把匿名读
    当作 ``AUTH_REQUIRED`` 正常终态返回，而不是抛异常。

    "不可用"在这里涵盖两类原因：平台自签令牌验不过，或 AS 令牌验不过（含**已撤销**）。
    两者对调用方的含义相同 —— 没有可用身份。区别只进日志。
    """
    if not token:
        return None
    claims = await resolve_access_claims(token)
    if isinstance(claims, TokenRejection):
        return None
    if isinstance(claims, AsAccessClaims):
        return principal_from_as_claims(claims)
    return principal_from_claims(claims)


async def principal_from_authorization_header(header_value: str | None) -> McpPrincipal | None:
    """``Authorization`` 头 → 主体。"""
    return await principal_from_bearer_token(bearer_token(header_value))


async def principal_from_headers(headers: Mapping[str, str] | None) -> McpPrincipal | None:
    """HTTP 头 → 主体。头名按小写匹配。

    不依赖 ``Mapping`` 的键大小写（不同客户端发 ``Authorization`` / ``authorization``
    的都有），因此先归一再取 —— 这也修掉了原先"两次 ``.get`` 只覆盖两种拼法"的写法。
    """
    if not headers:
        return None
    normalized = {str(key).lower(): value for key, value in headers.items()}
    return await principal_from_authorization_header(normalized.get("authorization"))
