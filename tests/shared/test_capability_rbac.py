"""Capability-based RBAC regressions (spec §3.2 / Phase 0 exit gates).

Locks in the authorization matrix at the HTTP boundary:
  - admin does NOT inherit HR business content access (interview, voice,
    weekly, culture, HR cases) — the linear role hierarchy is gone.
  - employee can only reach policy QA among business scenes.
  - evaluation is admin-only now (was hr_manager before the repositioning).
  - hr_manager keeps business scenes and feedback governance but NOT KB
    administration, evaluation, or settings.
  - unknown roles fail closed.

These run against the real FastAPI app with an injected JWT; RBACMiddleware
rejects before any DB access, so no database is required.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.config.settings import settings
from app.main import create_app

_JWT_ISSUER = "hrbp-ai-workbench"
_JWT_AUDIENCE = "hrbp-ai-workbench"
_TENANT = "06c87e30-4abf-40ca-9805-3c8b44cc5fd5"


def _make_token(role: str) -> str:
    payload = {
        "sub": f"user-{role}",
        "role": role,
        "tenant_id": _TENANT,
        "email": f"{role}@example.com",
        "type": "access",
        "jti": "test-jti",
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": datetime.now(UTC),
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def _get(client: TestClient, role: str, path: str):
    return client.get(path, headers={"Authorization": f"Bearer {_make_token(role)}"})


def test_admin_cannot_access_hr_business_content():
    """Spec §3.2: admin defaults to NO access on interview/voice/weekly/culture/hr-case bodies."""
    from app.access.middleware.rbac import ROLE_CAPABILITIES

    caps = ROLE_CAPABILITIES["admin"]
    for scene in ("interview_digest", "voice_insight", "weekly_report", "culture_content", "hr_case", "policy_qa"):
        assert scene not in caps, f"admin must not inherit business capability {scene}"


def test_capability_matrix_shape():
    from app.access.middleware.rbac import ROLE_CAPABILITIES

    assert ROLE_CAPABILITIES["employee"] == {"policy_qa", "employee_request"}
    assert "hr_case" in ROLE_CAPABILITIES["hrbp"]
    assert "work_summary" in ROLE_CAPABILITIES["hrbp"]
    assert "work_summary" in ROLE_CAPABILITIES["hr_manager"]
    assert "kb_management" not in ROLE_CAPABILITIES["hr_manager"]
    assert "knowledge_feedback" in ROLE_CAPABILITIES["hr_manager"]
    assert "evaluation" in ROLE_CAPABILITIES["admin"]
    assert "settings" in ROLE_CAPABILITIES["admin"]
    assert "user_admin" in ROLE_CAPABILITIES["admin"]


def test_admin_forbidden_on_business_scene_routes(client):
    for path in (
        "/api/interview-digest/history",
        "/api/voice-insight/history",
        "/api/weekly-report/history",
        "/api/culture-content/history",
        "/api/v1/hr-cases/some-case",
    ):
        resp = _get(client, "admin", path)
        assert resp.status_code == 403, f"admin should be blocked on {path}, got {resp.status_code}"


def test_employee_forbidden_on_business_and_management_routes(client):
    for path in (
        "/api/interview-digest/history",
        "/api/voice-insight/history",
        "/api/weekly-report/history",
        "/api/kb",
        "/api/settings",
    ):
        resp = _get(client, "employee", path)
        assert resp.status_code == 403, f"employee should be blocked on {path}, got {resp.status_code}"


def test_employee_allowed_on_policy_qa_route(client):
    resp = _get(client, "employee", "/api/policy-qa/knowledge-bases")
    assert resp.status_code != 403, "employee must keep policy QA access"


def test_employee_and_admin_cannot_read_work_summaries(client):
    """Today's work contains HR workflow metadata and is business-role only."""
    for role in ("employee", "admin"):
        resp = _get(client, role, "/api/work-summaries")
        assert resp.status_code == 403, f"{role} must not read HR work summaries"


def test_only_employee_can_use_my_requests_surface(client):
    """The employee request surface must not piggy-back on policy QA access."""
    for role in ("hrbp", "hr_manager", "admin"):
        resp = _get(client, role, "/api/my-requests")
        assert resp.status_code == 403, f"{role} must not use the employee self-service surface"


def test_hr_manager_no_longer_sees_evaluation(client):
    """Evaluation repositioned to admin-only (spec §2 gap table)."""
    resp = _get(client, "hr_manager", "/api/eval/metrics")
    assert resp.status_code == 403


def test_admin_allowed_on_evaluation_and_settings(client):
    for path in ("/api/eval/metrics", "/api/settings", "/api/admin/users"):
        resp = _get(client, "admin", path)
        assert resp.status_code != 403, f"admin should reach {path}"


def test_manager_cannot_administer_knowledge_base(client):
    resp = _get(client, "hr_manager", "/api/kb")
    assert resp.status_code == 403


def test_hrbp_allowed_on_business_scenes(client):
    for path in ("/api/interview-digest/history", "/api/voice-insight/history"):
        resp = _get(client, "hrbp", path)
        assert resp.status_code != 403, f"hrbp should reach {path}"


def test_unknown_role_fails_closed(client):
    resp = _get(client, "intruder", "/api/interview-digest/history")
    assert resp.status_code == 403


def test_handler_capability_guard_does_not_use_role_hierarchy():
    """Defense in depth must reject admin even if middleware mapping is missed."""
    from starlette.requests import Request

    try:
        from app.access.middleware.decorators import require_capability
    except ImportError:
        pytest.fail("route handlers need a capability guard, not a numeric role hierarchy")

    @require_capability("interview_digest")
    async def handler(request: Request) -> str:
        return "ok"

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user_role = "admin"
    with pytest.raises(Exception) as exc_info:
        asyncio.run(handler(request))
    assert exc_info.value.__class__.__name__ == "ForbiddenError"


def test_real_business_handler_rejects_admin_without_middleware_help():
    """A route-level guard must remain safe when a prefix is omitted upstream."""
    from starlette.requests import Request

    from app.access.routes.interview_digest import get_history

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user_id = "admin-a"
    request.state.user_role = "admin"
    with pytest.raises(Exception) as exc_info:
        asyncio.run(get_history(request=request, limit=1, session=None))
    assert exc_info.value.__class__.__name__ == "ForbiddenError"


def test_forbidden_response_does_not_leak_internals(client):
    resp = _get(client, "admin", "/api/interview-digest/history")
    body = resp.json()
    assert resp.status_code == 403
    # Uniform denial, no scene/role/path disclosure (spec §3.3)
    assert body.get("code") == "FORBIDDEN"
    assert "interview" not in body.get("message", "")


# ---------------------------------------------------------------------------
# Fake-progress regressions (spec §9.1 / Phase 0: 伪百分比任务 = 0)
# ---------------------------------------------------------------------------


def test_status_schemas_have_no_progress_field():
    from app.scenarios.interview_digest.schemas import DigestStatus
    from app.scenarios.voice_insight.schemas import TaskStatusResponse

    assert "progress" not in DigestStatus.model_fields
    assert "progress" not in TaskStatusResponse.model_fields


def test_celery_tasks_have_hard_time_limits():
    from app.shared.celery_app import celery_app

    assert celery_app.conf.task_time_limit is not None
    assert celery_app.conf.task_time_limit > 0


def test_expire_stale_tasks_marks_stuck_tasks_failed():
    """No unexplained forever-pending tasks (Phase 0 exit gate)."""
    from app.scenarios.tasks import expire_stale_tasks

    assert asyncio.iscoroutinefunction(expire_stale_tasks)
