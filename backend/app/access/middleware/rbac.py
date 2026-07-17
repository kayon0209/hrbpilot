"""HRBP AI Workbench — RBAC authorization middleware.

Scene-level visibility matrix:
  Employee → policy_qa only
  HRBP → all 5 scenarios
  HR Manager → all 5 + KB management + evaluation
  Admin → all + settings
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.shared.logger import get_logger

logger = get_logger(__name__)

# RBAC scene visibility matrix
SCENE_VISIBILITY = {
    "employee": {"policy_qa"},
    "hrbp": {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"},
    "hr_manager": {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"},
    "admin": {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"},
}

# Management page visibility
MANAGEMENT_VISIBILITY = {
    "employee": set(),
    "hrbp": set(),
    "hr_manager": {"kb_management", "evaluation"},
    "admin": {"kb_management", "evaluation", "settings"},
}

# Route prefix to scene mapping
SCENE_ROUTE_MAP = {
    "/api/policy-qa": "policy_qa",
    "/api/interview-digest": "interview_digest",
    "/api/voice-insight": "voice_insight",
    "/api/weekly-report": "weekly_report",
    "/api/culture-content": "culture_content",
}

MANAGEMENT_ROUTE_MAP = {
    "/api/kb": "kb_management",
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


class RBACMiddleware(BaseHTTPMiddleware):
    """RBAC authorization — check role vs scene/management page access."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip public paths
        if path in PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)

        # Skip if no auth context (AuthMiddleware should have set this)
        role = getattr(request.state, "user_role", None)
        if not role:
            return await call_next(request)

        # Check scene routes
        for route_prefix, scene_id in SCENE_ROUTE_MAP.items():
            if path.startswith(route_prefix):
                allowed_scenes = SCENE_VISIBILITY.get(role, set())
                if scene_id not in allowed_scenes:
                    logger.warning(
                        "rbac_forbidden",
                        role=role,
                        scene=scene_id,
                        path=path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "FORBIDDEN",
                            "status": 403,
                            "message": f"Role '{role}' cannot access scenario '{scene_id}'",
                            "required_role": "hrbp or above",
                        },
                    )
                break

        # Check management routes
        for route_prefix, mgmt_id in MANAGEMENT_ROUTE_MAP.items():
            if path.startswith(route_prefix):
                allowed_mgmt = MANAGEMENT_VISIBILITY.get(role, set())
                if mgmt_id not in allowed_mgmt:
                    logger.warning(
                        "rbac_forbidden_mgmt",
                        role=role,
                        mgmt=mgmt_id,
                        path=path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "FORBIDDEN",
                            "status": 403,
                            "message": f"Role '{role}' cannot access management page '{mgmt_id}'",
                            "required_role": "hr_manager or admin",
                        },
                    )
                break

        return await call_next(request)
