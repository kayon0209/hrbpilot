"""HRBP AI Workbench — capability-based authorization middleware.

Model (spec §3.2): a role holds a SET of capabilities; nothing is inherited
by "being above" another role. In particular the platform admin does NOT
get HR business content access (interviews, voice, weekly, HR cases) by
default — those require an explicit business role. An admin who also needs
business access should hold a business role.

Routing here is the coarse gate (spec §3.3): object-level authorization is
enforced in the service layer, tenant_id is an isolation boundary only.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.shared.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Capability sets per role — no linear hierarchy, no inheritance.
# ---------------------------------------------------------------------------
ROLE_CAPABILITIES: dict[str, set[str]] = {
    "employee": {
        "policy_qa",  # own-visibility scope enforced by service layer
        "employee_request",
    },
    "hrbp": {
        "policy_qa",
        "interview_digest",
        "voice_insight",
        "weekly_report",
        "culture_content",
        "hr_case",  # object-level ACL still applies (service layer)
        "hr_request_triage",
        "work_summary",
    },
    "hr_manager": {
        "policy_qa",
        "interview_digest",
        "voice_insight",
        "weekly_report",
        "culture_content",
        "hr_case",
        "knowledge_feedback",  # manager action center (Phase 3)
        "hr_request_triage",
        "work_summary",
    },
    "admin": {
        # Platform capabilities only — no HR business content by default.
        "kb_management",
        "evaluation",
        "settings",
        "audit_read",
        "data_source_admin",
        "user_admin",
    },
}

# Kept as thin views over the capability sets so existing imports keep working.
SCENE_VISIBILITY = {
    "employee": {"policy_qa"},
    "hrbp": {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"},
    "hr_manager": {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"},
    # NOTE: admin intentionally absent from business scenes.
    "admin": set(),
}

MANAGEMENT_VISIBILITY = {
    "employee": set(),
    "hrbp": set(),
    "hr_manager": {"knowledge_feedback"},
    "admin": {"kb_management", "evaluation", "settings"},
}

# Route prefix → required capability
ROUTE_CAPABILITY_MAP = {
    "/api/policy-qa": "policy_qa",
    "/api/my-requests": "employee_request",
    "/api/hr-requests": "hr_request_triage",
    "/api/work-summaries": "work_summary",
    "/api/interview-digest": "interview_digest",
    "/api/voice-insight": "voice_insight",
    "/api/weekly-report": "weekly_report",
    "/api/culture-content": "culture_content",
    "/api/v1/hr-cases": "hr_case",
    "/api/kb": "kb_management",
    "/api/knowledge-feedback": "knowledge_feedback",
    "/api/eval": "evaluation",
    "/api/settings": "settings",
    "/api/audit": "audit_read",
    "/api/data-sources": "data_source_admin",
    "/api/admin/users": "user_admin",
}

# Legacy aliases kept for internal callers
SCENE_ROUTE_MAP = {
    "/api/policy-qa": "policy_qa",
    "/api/my-requests": "employee_request",
    "/api/hr-requests": "hr_request_triage",
    "/api/work-summaries": "work_summary",
    "/api/interview-digest": "interview_digest",
    "/api/voice-insight": "voice_insight",
    "/api/weekly-report": "weekly_report",
    "/api/culture-content": "culture_content",
}

MANAGEMENT_ROUTE_MAP = {
    "/api/kb": "kb_management",
    "/api/knowledge-feedback": "knowledge_feedback",
    "/api/eval": "evaluation",
    "/api/settings": "settings",
}

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


def _forbidden(role: str, capability: str, path: str) -> JSONResponse:
    logger.warning("rbac_forbidden", role=role, capability=capability, path=path)
    return JSONResponse(
        status_code=403,
        content={
            "code": "FORBIDDEN",
            "status": 403,
            "message": "当前角色无权使用此功能",
            "required_role": capability,
        },
    )


class RBACMiddleware(BaseHTTPMiddleware):
    """Coarse capability gate at the route level.

    Object-level authorization and tenant isolation are NOT done here — they
    belong to the service layer (spec §3.3).

    NOTE: each BaseHTTPMiddleware sees its own copy of request.state, so we
    cannot rely on AuthMiddleware having set user_role there. Instead we
    decode the JWT payload ourselves (it is already verified upstream; an
    invalid token never reaches this middleware). The role lives in
    ``request.scope["auth"]`` after AuthMiddleware stores the decoded
    payload — kept compatible by reading state first, then scope.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)

        role = self._resolve_role(request)
        if not role:
            return await call_next(request)

        capabilities = ROLE_CAPABILITIES.get(role)
        if capabilities is None:
            # Unknown role: deny everything that maps to a capability.
            capabilities = set()

        for route_prefix, capability in ROUTE_CAPABILITY_MAP.items():
            if path.startswith(route_prefix):
                if capability not in capabilities:
                    return _forbidden(role, capability, path)
                break

        return await call_next(request)

    @staticmethod
    def _resolve_role(request: Request) -> str | None:
        # BaseHTTPMiddleware state copies are per-layer; AuthMiddleware exports
        # its decoded payload on the (shared) scope for downstream layers.
        auth = request.scope.get("auth")
        if isinstance(auth, dict):
            role = auth.get("role")
            if isinstance(role, str) and role:
                return role
        return getattr(request.state, "user_role", None)
