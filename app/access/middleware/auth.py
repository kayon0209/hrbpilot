"""HRBP AI Workbench — JWT authentication middleware.

Verifies access tokens from Authorization header.
Sets user_id, role, tenant_id in request state for downstream use.
"""

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication — verify access token, set user context in request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/api/connector-webhooks"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
        except JWTError as e:
            logger.warning("jwt_decode_failed", error=str(e))
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Invalid or expired token"},
            )

        if payload.get("type") != "access":
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Not an access token"},
            )

        user_id = payload.get("sub")
        role = payload.get("role")
        tenant_id = payload.get("tenant_id")
        if not isinstance(user_id, str) or not user_id:
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Invalid token subject"},
            )
        if not isinstance(role, str) or not role:
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Invalid token role"},
            )
        if not isinstance(tenant_id, str) or not tenant_id:
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Invalid token tenant"},
            )

        request.state.user_id = user_id
        request.state.user_role = role
        request.state.tenant_id = tenant_id
        request.state.email = payload.get("email", "")

        # BaseHTTPMiddleware layers each get a copy of request.state; the
        # scope dict is the only place shared across middleware layers.
        # RBACMiddleware reads the role from here.
        request.scope["auth"] = {
            "user_id": user_id,
            "role": role,
            "tenant_id": tenant_id,
        }

        return await call_next(request)
