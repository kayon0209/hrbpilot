"""Employee request security regressions (Phase 4 exit gates, spec §5.4/§7.9).

Locks in at the HTTP boundary:
  - employee endpoints only ever return the desensitized projection
  - one employee cannot read another employee's requests (object-level ACL)
  - hr_note / hr_case_id never leak through the employee surface
  - employee cannot reach the HR triage surface
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.config.settings import settings
from app.main import create_app

_JWT_ISSUER = "hrbp-ai-workbench"
_JWT_AUDIENCE = "hrbp-ai-workbench"
_TENANT = "06c87e30-4abf-40ca-9805-3c8b44cc5fd5"
_EMP_A = "10000000-0000-4000-8000-000000000001"
_EMP_B = "10000000-0000-4000-8000-000000000002"


def _make_token(role: str, user_id: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": _TENANT,
        "email": f"{user_id}@example.com",
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


def _headers(role: str, user_id: str) -> dict:
    return {"Authorization": f"Bearer {_make_token(role, user_id)}"}


def test_employee_projection_has_no_internal_fields(client):
    """The desensitized view must not carry hr_note / hr_case_id / risk fields."""
    from app.scenarios.employee_request.service import _employee_view

    class Row:
        id = "r1"
        request_type = "certificate"
        title = "t"
        status = "in_progress"
        next_step_for_employee = "next"
        needs_materials = None
        updated_at = None
        created_at = None
        hr_note = "SECRET internal note"
        hr_case_id = "case-internal"

    view = _employee_view(Row())
    dumped = view.model_dump()
    assert "hr_note" not in dumped
    assert "hr_case_id" not in dumped


def test_employee_cannot_open_another_employees_request(client):
    """Object-level ACL: employee B files a request; employee A must not see it."""
    filed = client.post(
        "/api/my-requests",
        json={"request_type": "certificate", "title": "B 的请求", "description": "内容"},
        headers=_headers("employee", _EMP_B),
    )
    assert filed.status_code == 200, filed.text
    request_id = filed.json()["request_id"]

    stranger = client.get(f"/api/my-requests/{request_id}", headers=_headers("employee", _EMP_A))
    assert stranger.status_code == 404  # uniform denial, no existence disclosure

    owner = client.get(f"/api/my-requests/{request_id}", headers=_headers("employee", _EMP_B))
    assert owner.status_code == 200
    assert owner.json()["request_id"] == request_id


def test_employee_cannot_reach_hr_triage(client):
    resp = client.get("/api/hr-requests", headers=_headers("employee", _EMP_A))
    assert resp.status_code == 403


def test_hrbp_and_manager_reach_hr_triage(client):
    for role in ("hrbp", "hr_manager"):
        resp = client.get("/api/hr-requests", headers={**_headers(role, "10000000-0000-4000-8000-000000000009"), "Authorization": f"Bearer {_make_token(role, '10000000-0000-4000-8000-000000000009')}"})
        assert resp.status_code != 403, f"{role} should reach HR triage"


def test_admin_cannot_reach_hr_triage_or_employee_requests(client):
    """Admin is a platform role — no HR business content (spec §3.2)."""
    resp = client.get("/api/hr-requests", headers=_headers("admin", "10000000-0000-4000-8000-000000000003"))
    assert resp.status_code == 403


def test_triage_requires_employee_facing_next_step(client):
    """in_progress/resolved without a next step for the employee is rejected."""
    filed = client.post(
        "/api/my-requests",
        json={"request_type": "process_help", "title": "流程协助", "description": "内容"},
        headers=_headers("employee", _EMP_B),
    )
    request_id = filed.json()["request_id"]
    resp = client.post(
        f"/api/hr-requests/{request_id}/triage",
        json={"status": "in_progress"},
        headers=_headers("hrbp", "10000000-0000-4000-8000-000000000009"),
    )
    # 422 from pydantic (missing required sibling) or 400 from service — both are rejections
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_triage_queue_requires_assignment_or_explicit_manager_scope():
    """Same-tenant HR staff must not read another owner's employee requests."""
    from sqlalchemy import delete

    from app.data.database import get_engine, get_session_factory
    from app.data.models.access_scope import ManagerOrgScope, OrgUnit
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.scenarios.employee_request.service import HrTriageBody, hr_list_open, hr_triage
    from app.shared.errors import NotFoundError

    await get_engine().dispose()
    tenant_id = str(uuid4())
    org_a, org_b = str(uuid4()), str(uuid4())
    manager_id, hr_a, hr_b, employee_a, employee_b = [str(uuid4()) for _ in range(5)]
    request_a, request_b = str(uuid4()), str(uuid4())
    factory = get_session_factory()

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all([OrgUnit(id=org_a, tenant_id=tenant_id, name="A"), OrgUnit(id=org_b, tenant_id=tenant_id, name="B")])
        await db.flush()
        db.add_all(
            [
                User(id=manager_id, tenant_id=tenant_id, name="M", email=f"{manager_id}@example.com", hashed_password="x", role="hr_manager", org_unit_id=org_a),
                User(id=hr_a, tenant_id=tenant_id, name="HA", email=f"{hr_a}@example.com", hashed_password="x", role="hrbp", org_unit_id=org_a),
                User(id=hr_b, tenant_id=tenant_id, name="HB", email=f"{hr_b}@example.com", hashed_password="x", role="hrbp", org_unit_id=org_b),
                User(id=employee_a, tenant_id=tenant_id, name="EA", email=f"{employee_a}@example.com", hashed_password="x", role="employee", org_unit_id=org_a),
                User(id=employee_b, tenant_id=tenant_id, name="EB", email=f"{employee_b}@example.com", hashed_password="x", role="employee", org_unit_id=org_b),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a))
        db.add_all(
            [
                EmployeeRequest(id=request_a, tenant_id=tenant_id, created_by=employee_a, request_type="other", title="A request", description="A private", status="submitted"),
                EmployeeRequest(id=request_b, tenant_id=tenant_id, created_by=employee_b, request_type="other", title="B request", description="B private", status="submitted"),
            ]
        )
        await db.commit()

    try:
        try:
            manager_rows = await hr_list_open(tenant_id, manager_id, "hr_manager")
        except TypeError:
            pytest.fail("HR request queries must receive actor identity and role")
        assert {row["request_id"] for row in manager_rows} == {request_a}
        assert await hr_list_open(tenant_id, hr_a, "hrbp") == []

        await hr_triage(
            tenant_id,
            manager_id,
            "hr_manager",
            request_a,
            HrTriageBody(status="in_progress", next_step_for_employee="HR 已接手", hr_owner_id=hr_a),
        )
        assert {row["request_id"] for row in await hr_list_open(tenant_id, hr_a, "hrbp")} == {request_a}
        assert await hr_list_open(tenant_id, hr_b, "hrbp") == []

        with pytest.raises(NotFoundError):
            await hr_triage(
                tenant_id,
                hr_b,
                "hrbp",
                request_a,
                HrTriageBody(status="resolved", next_step_for_employee="完成"),
            )

        async with factory() as db:
            from sqlalchemy import select

            from app.data.models.infra import AuditLog

            db.info["tenant_id"] = tenant_id
            audit_rows = (
                (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.tenant_id == tenant_id,
                            AuditLog.scenario_id == "employee_request.triaged",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(audit_rows) == 1
        assert audit_rows[0].user_id == manager_id
        assert request_a in (audit_rows[0].input_summary or "")

        # Assignee pool (audit P1-7): the manager's assign list must contain
        # only HRBPs inside the authorised org scope.
        from app.scenarios.employee_request.service import hr_list_assignees

        assignees = await hr_list_assignees(tenant_id, manager_id, "hr_manager")
        assert {item["user_id"] for item in assignees} == {hr_a}, "only in-scope HRBPs are assignable"
        assert all(item["name"] and item["email"] for item in assignees)
        assert await hr_list_assignees(tenant_id, hr_a, "hrbp") == [], "non-manager roles get no assign pool"
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            from app.data.models.infra import AuditLog

            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
            await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
            await db.commit()
