"""HRBP AI Workbench — JWT authentication middleware.

Verifies access tokens from Authorization header.
Sets user_id, role, tenant_id in request state for downstream use.

解码与 claim 校验本身不在本文件 —— 它收敛在 ``app.access.tokens``。
这里只负责"怎么向调用方表达拒绝"（带具体文案的 401）以及把已验证身份写进
``request.state`` / ``request.scope``。
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.access.tokens import (
    SCOPE_AUTH_METHOD_INTERNAL,
    TokenRejection,
    bearer_token,
    read_access_claims,
)
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
    # MCP Streamable HTTP 在传输层整体跳过这里（工具层自己解析 Bearer 令牌）。
    # 注意：**不是**"匿名读放行"。匿名调用读工具只会拿到 AUTH_REQUIRED 且不含
    # 任何真实数据；带身份调用的判定见 app/mcp/auth/authorization.py。
    # 传输层 401 挑战（标准 MCP OAuth 自动发现所需）尚未实现。
    "/mcp",
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
}


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"code": "AUTH_ERROR", "status": 401, "message": message},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication — verify access token, set user context in request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/api/connector-webhooks")
            or path.startswith("/mcp")
        ):
            return await call_next(request)

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
