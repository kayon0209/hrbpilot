"""HRBP AI Workbench — Tenant context middleware.

Extracts tenant_id from JWT claims or X-Tenant-ID header,
sets PostgreSQL session variable for RLS policies.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.shared.errors import AuthError
from app.shared.logger import get_logger

logger = get_logger(__name__)


def require_tenant_id(request: Request) -> str:
    """Return the authenticated tenant or fail closed.

    Route handlers use this instead of silently substituting a real fallback
    tenant when middleware state is absent.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, str) or not tenant_id:
        raise AuthError("Tenant context is required")
    return tenant_id


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Resolve tenant_id and store in request state for RLS."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Do not trust tenant selection from a client-controlled header. The
        # authenticated tenant comes from the JWT in AuthMiddleware.
        if not getattr(request.state, "tenant_id", None):
            tenant_id = request.headers.get("X-Tenant-ID")
            if tenant_id:
                request.state.requested_tenant_id = tenant_id

        response = await call_next(request)
        return response
