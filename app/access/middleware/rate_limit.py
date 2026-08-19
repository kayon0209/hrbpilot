"""HRBP AI Workbench — request rate limiting middleware."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.guardrails.rate_limiter import RateLimiter
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger

logger = get_logger(__name__)

PUBLIC_PATH_PREFIXES = (
    "/api/health",
    "/api/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/dev-users",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests that exceed tenant or user quotas."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._limiter = RateLimiter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path == prefix or path.startswith(prefix + "/") for prefix in PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)
        if not tenant_id or not user_id:
            return JSONResponse(
                status_code=403, content={"code": "FORBIDDEN", "status": 403, "message": "Missing user context"}
            )

        try:
            await self._limiter.check(str(tenant_id), str(user_id))
        except RateLimitError as exc:
            logger.warning("request_rate_limited", path=path, tenant_id=tenant_id, user_id=user_id)
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "status": exc.status_code, "message": exc.message},
            )

        return await call_next(request)
