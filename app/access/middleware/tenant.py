"""HRBP AI Workbench — Tenant context middleware.

Extracts tenant_id from JWT claims or X-Tenant-ID header,
sets PostgreSQL session variable for RLS policies.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.shared.logger import get_logger

logger = get_logger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Resolve tenant_id and store in request state for RLS."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Priority: JWT claim > X-Tenant-ID header > default
        tenant_id = None

        # JWT claims are set by auth middleware (runs after this)
        # So we check header first; auth middleware will override if present
        tenant_id = request.headers.get("X-Tenant-ID")

        if tenant_id:
            request.state.tenant_id = tenant_id
        else:
            # Will be set by auth middleware later if user is authenticated
            request.state.tenant_id = "default"

        response = await call_next(request)
        return response
