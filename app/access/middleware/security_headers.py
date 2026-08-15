"""HRBP AI Workbench — Security headers middleware.

Applies security-related HTTP headers to every response:
  - Content-Security-Policy (CSP)
  - Strict-Transport-Security (HSTS)
  - X-Content-Type-Options
  - X-Frame-Options
  - X-XSS-Protection
  - Referrer-Policy
  - Permissions-Policy

Headers are configurable via settings and can be tightened in production.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Default security headers — tighten in production
_SECURITY_HEADERS: dict[str, str] = {
    # CSP: restrict script/style sources; expandable per-environment
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
    ),
    # HSTS: enforce HTTPS (set max-age=0 in dev to avoid browser lock-in)
    "Strict-Transport-Security": (
        "max-age=0; includeSubDomains"
        if not settings.is_production
        else "max-age=31536000; includeSubDomains; preload"
    ),
    # Prevent MIME-type sniffing
    "X-Content-Type-Options": "nosniff",
    # Prevent clickjacking
    "X-Frame-Options": "DENY",
    # Legacy XSS protection (for older browsers)
    "X-XSS-Protection": "1; mode=block",
    # Limit referrer information leakage
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Restrict browser features
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), "
        "interest-cohort=()"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every HTTP response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        for header_name, header_value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header_name, header_value)

        return response
