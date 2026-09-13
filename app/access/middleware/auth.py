"""HRBP AI Workbench — JWT authentication middleware.

Verifies access tokens from Authorization header.
Sets user_id, role, tenant_id in request state for downstream use.

解码与 claim 校验本身不在本文件 —— 平台自签令牌收敛在 ``app.access.tokens``，
AS 签发令牌收敛在 ``app.access.as_tokens``（并**按 ``iss`` 分流**，入口
``as_tokens.resolve_access_claims``）。这里只负责"怎么向调用方表达拒绝"以及把已验证
身份写进 ``request.state`` / ``request.scope``。

两类路径接受的凭据**刻意不同**
------------------------------
- ``/mcp``（外部 Agent 入口）：两类都收 —— AS 令牌是正路，平台令牌由过渡开关控制。
- 其余所有 ``/api/*``（含 REST 桥 ``/api/mcp``、网页工作台）：**只收平台自签令牌**。
  这里仍走 ``read_access_claims``，它用 HS256 校验，AS 的 ES256 令牌必然验不过。

这不是不一致：``/api/mcp`` 是工作台 UI 的内部接口，不是外部 Agent 的入口（ADR-0002
§4）。而它即使收到一个 AS 令牌也无法安全处理 —— 它只能从 claim 重建主体，拿不到
客户端与授权上限，那会把客户端上限静默丢掉。让它在验签阶段就失败，比让它持有一个
残缺的主体更安全。

两种"拒绝的表达"，不可互换
--------------------------
1. **业务信封**（``AUTH_ERROR`` + 401 JSON）：工作台 UI 与 ``/api/*`` 用。UI 需要
   一个可渲染的结构化错误，且它自己会处理"未登录 → 跳登录页"。
2. **RFC 9728 挑战**（401 + ``WWW-Authenticate``）：``/mcp`` 用。外部 Agent 的自动
   发现**完全依赖**这个头 —— 没有它，客户端不知道去哪里拿令牌，只能放弃。

两者都是 401，但互换即失效：给 UI 一个挑战头没有用，给 MCP 客户端一个业务信封
则等于没有挑战（信封里不含资源元数据地址）。
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.access.as_tokens import AsAccessClaims, resolve_access_claims
from app.access.protocol_paths import WELL_KNOWN_PREFIX
from app.access.resource_metadata import BEARER_ERROR_INVALID_TOKEN, www_authenticate_challenge
from app.access.tokens import (
    SCOPE_AUTH_METHOD_INTERNAL,
    SCOPE_AUTH_METHOD_OAUTH,
    TokenRejection,
    bearer_token,
    read_access_claims,
)
from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)

PUBLIC_PATHS = [
    "/api/health",
    "/api/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/refresh",
    # Provider callbacks authenticate via their platform signature; the webhook
    # routes verify it BEFORE touching state (no JWT in a webhook request).
    "/api/connector-webhooks",
]

#: 拒绝类别 → 对外文案。刻意不说明是哪个 claim 有问题：对调用方没有可操作性，
#: 对探测者反倒是信息。具体原因进日志。
_REJECTION_MESSAGES: dict[TokenRejection, str] = {
    TokenRejection.MALFORMED: "Invalid or expired token",
    TokenRejection.WRONG_TYPE: "Not an access token",
    TokenRejection.INCOMPLETE: "Invalid token claims",
    TokenRejection.REVOKED: "Token has been revoked",
}


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"code": "AUTH_ERROR", "status": 401, "message": message},
    )


def _mcp_challenge(error: str | None = None) -> JSONResponse:
    """401 + RFC 9728 挑战：把客户端导向可匿名读取的元数据文档。

    ``resource_metadata`` 同时出现在响应头与响应体里。头是给客户端自动发现用的
    （规范路径）；体是冗余的，但让运维用 ``curl`` 打这个端点时不必再去翻响应头 ——
    排查 MCP 接入问题时这一步会被反复做。
    """
    return JSONResponse(
        status_code=401,
        content={
            "code": "AUTH_ERROR",
            "status": 401,
            "message": "Authentication required",
            "resource_metadata": settings.protected_resource_metadata_url,
        },
        headers={"WWW-Authenticate": www_authenticate_challenge(error=error)},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication — verify access token, set user context in request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/api/connector-webhooks")
            # RFC 9728 元数据必须匿名可读：它是客户端**在被 401 拒绝之后**才能拿到
            # 的东西。要求凭据才能读会让发现流程死锁（要令牌才知道去哪拿令牌）。
            or path.startswith(WELL_KNOWN_PREFIX)
        ):
            return await call_next(request)

        # MCP 协议出口走挑战式认证，而不是业务信封 —— 见 _dispatch_mcp。
        if path == "/mcp" or path.startswith("/mcp/"):
            return await self._dispatch_mcp(request, call_next)

        token = bearer_token(request.headers.get("Authorization"))
        if token is None:
            return _unauthorized("Missing or invalid Authorization header")

        claims = read_access_claims(token)
        if isinstance(claims, TokenRejection):
            if claims is TokenRejection.MALFORMED:
                logger.warning("jwt_decode_failed")
            else:
                logger.warning("jwt_claims_rejected", reason=claims.value)
            return _unauthorized(_REJECTION_MESSAGES[claims])

        request.state.user_id = claims.user_id
        request.state.user_role = claims.role
        request.state.tenant_id = claims.tenant_id
        request.state.email = claims.email

        # BaseHTTPMiddleware layers each get a copy of request.state; the
        # scope dict is the only place shared across middleware layers.
        # RBACMiddleware reads the role from here.
        request.scope["auth"] = {
            "user_id": claims.user_id,
            "role": claims.role,
            "tenant_id": claims.tenant_id,
            # 凭据来源：本中间件只接受平台自签 access token。下游（MCP REST 桥）
            # 需要这个事实来决定能不能重建主体 —— 见 app/access/tokens.py。
            "auth_method": SCOPE_AUTH_METHOD_INTERNAL,
        }

        return await call_next(request)

    async def _dispatch_mcp(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """``/mcp`` 的传输层认证：挑战式（RFC 9728 §5.1）。

        与其它路径唯一的差别是**怎么表达拒绝**：标准 401 加 ``WWW-Authenticate``，
        而不是工作台的业务信封。MCP 客户端的自动发现完全依赖这个挑战 —— 在拿到
        401 与 ``resource_metadata`` 之前，它没有任何途径知道该去哪个授权服务器。

        刻意**不**在这里判定授权（角色 / scope / 工具）：一次 HTTP 请求可能只是
        ``initialize`` 或 ``tools/list``，并不对应任何具体工具，传输层回答不了
        "这份凭据能不能调那个工具"。那属于工具调用层
        （``app.mcp.auth.authorize_tool_call``），两条出口在那里共用同一判定。

        这一层接受**两类**凭据，由 ``resolve_access_claims`` 按 ``iss`` 分流：

        - **AS 签发的 access token**（ES256 + JWKS + 撤销表）—— 外部 Agent 的正路。
        - **平台自签 JWT**（网页登录那一套），受 ``mcp_accepts_platform_tokens`` 控制。
          这是过渡开关：AS 上线后置 false，本出口就只接受 audience 绑定到 MCP resource
          的令牌（ADR-0002 §4）。置 false 后内部会话令牌在这里被**策略**拒绝，而不是
          因为签名验不过 —— 两者分不同的日志，否则运维会把"策略收紧"误读成"令牌损坏"。
        """
        token = bearer_token(request.headers.get("Authorization"))
        if token is None:
            # 没带凭据**不是错误**：客户端正是靠这个 401 开始发现流程，所以这里
            # 不带 error 参数 —— 标成 invalid_token 会让它以为自己的令牌坏了。
            return _mcp_challenge()

        claims = await resolve_access_claims(token)

        if isinstance(claims, AsAccessClaims):
            request.scope["auth"] = {
                "user_id": claims.user_id,
                "role": claims.role,
                "tenant_id": claims.tenant_id,
                "auth_method": SCOPE_AUTH_METHOD_OAUTH,
                # 客户端与安装实例维度：限流要按它们分别计数（方案 §WP3），而这里
                # 是唯一一处已经解过令牌的地方 —— 把它们写进 scope，下游限流就不必
                # 为了拿这两个值再解析一次令牌（那会多一次数据库查询）。
                "client_id": claims.client_id,
                "installation_id": claims.family_id,
            }
            return await call_next(request)

        if isinstance(claims, TokenRejection):
            logger.warning("mcp_token_rejected", reason=claims.value)
            return _mcp_challenge(error=BEARER_ERROR_INVALID_TOKEN)

        if not settings.mcp_accepts_platform_tokens:
            # 平台令牌在此出口被**策略**拒绝（它能被辨认，只是不被这个出口接受）。
            # 与上面那条分开记日志：否则运维会把"策略收紧"误读成"用户令牌过期"，
            # 去排查一个根本不存在的凭据问题。
            logger.warning("mcp_platform_token_refused_by_policy", user_id=claims.user_id)
            return _mcp_challenge(error=BEARER_ERROR_INVALID_TOKEN)

        request.scope["auth"] = {
            "user_id": claims.user_id,
            "role": claims.role,
            "tenant_id": claims.tenant_id,
            "auth_method": SCOPE_AUTH_METHOD_INTERNAL,
        }
        return await call_next(request)
