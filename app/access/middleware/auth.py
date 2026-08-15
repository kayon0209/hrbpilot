"""HRBP AI Workbench — JWT authentication middleware.

Verifies access tokens from Authorization header.
Sets user_id, role, tenant_id in request state for downstream use.
"""

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.config.settings import settings
from app.shared.errors import AuthError, ForbiddenError
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = [
    "/api/health",
    "/api/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/dev-users",
]


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication — verify access token, set user context in request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for public paths
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]  # Strip "Bearer "
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
        except JWTError as e:
            logger.warning("jwt_decode_failed", error=str(e))
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_ERROR", "status": 401, "message": "Invalid or expired token"},
            )

        # Set user context in request state
        request.state.user_id = payload.get("sub", "")
        request.state.user_role = payload.get("role", "employee")
        request.state.tenant_id = payload.get("tenant_id", "default")
        request.state.email = payload.get("email", "")

        return await call_next(request)
