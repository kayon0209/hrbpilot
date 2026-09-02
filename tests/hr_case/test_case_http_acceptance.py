"""Real authenticated HTTP acceptance for HRCase object authorization.

Covers the three journeys the independent review demanded before READY:
same-tenant cross-user case reads/mutations, cross-case approval binding,
and manager organisation scope. All requests carry real JWTs through the
real middleware stack against the real database via httpx ASGITransport.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.hr_case import (
    AgentRun,
    ApprovalRequest,
    CaseEvent,
    CasePlan,
    HRCase,
    ToolExecution,
)
from app.data.models.user import User

pytestmark = pytest.mark.skipif(
    not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS"),
    reason="set HRBP_RUN_DB_SECURITY_TESTS=true for real-database HTTP acceptance",
)


def _token(tenant_id: str, user_id: str, role: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": user_id,
                "role": role,
                "tenant_id": tenant_id,
                "email": f"{user_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(CaseEvent).where(CaseEvent.tenant_id == tenant_id))
        await db.execute(delete(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
        await db.execute(delete(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id))
        await db.execute(delete(CasePlan).where(CasePlan.tenant_id == tenant_id))
        await db.execute(delete(AgentRun).where(AgentRun.tenant_id == tenant_id))
        await db.execute(delete(HRCase).where(HRCase.tenant_id == tenant_id))
        await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
        await db.commit()


async def _seed_users(tenant_id: str) -> dict[str, str]:
    """HRBP A (org A), HRBP B (org B), one manager scoped to org A."""
    hrbp_a, hrbp_b, manager_id = (str(uuid4()) for _ in range(3))
    org_a, org_b = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                OrgUnit(id=org_a, tenant_id=tenant_id, name="华东"),
                OrgUnit(id=org_b, tenant_id=tenant_id, name="华南"),
                User(
                    id=hrbp_a,
                    tenant_id=tenant_id,
                    name="A",
                    email=f"{hrbp_a}@example.test",
                    hashed_password="x",
                    role="hrbp",
                    org_unit_id=org_a,
                ),
                User(
                    id=hrbp_b,
                    tenant_id=tenant_id,
                    name="B",
                    email=f"{hrbp_b}@example.test",
                    hashed_password="x",
                    role="hrbp",
                    org_unit_id=org_b,
                ),
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="M",
                    email=f"{manager_id}@example.test",
                    hashed_password="x",
                    role="hr_manager",
                    org_unit_id=org_a,
                ),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a))
        await db.commit()
    return {"hrbp_a": hrbp_a, "hrbp_b": hrbp_b, "manager": manager_id}


_APP_CACHE: dict[str, object] = {}


def _shared_app():
    """One app instance per test process: repeated lifespan startups each
    spawn engine pools whose late GC triggers 'Event loop is closed' noise on
    Windows when pytest tears the loop down."""
    if "app" not in _APP_CACHE:
        from app.main import create_app

        _APP_CACHE["app"] = create_app()
    return _APP_CACHE["app"]


class _Http:
    """Real async HTTP over the ASGI transport (no synchronous TestClient)."""

    def __init__(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_shared_app()),
            base_url="http://test",
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def _run(self, token: str, method: str, path: str, json_body: dict | None = None):
        return await self._client.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=json_body)

    async def create_case(self, token: str, **payload):
        return await self._run(token, "POST", "/api/v1/hr-cases", payload)

    async def get_case(self, token: str, case_id: str):
        return await self._run(token, "GET", f"/api/v1/hr-cases/{case_id}")

    async def plan(self, token: str, case_id: str, goal: str):
        # A write step makes run_plan stop for approval — the journey under test.
        import json as _json

        proposal = _json.dumps(
            {
                "steps": [
                    {
                        "tool": "create_hr_case",
                        "params": {"title": "验收子案例", "subject_ref": "EMP-ACC", "category": "overtime"},
                        "reason": "acceptance write step",
                        "expected": "child case",
                    }
                ],
                "rationale": "acceptance journey",
                "risk_notes": "none",
            },
            ensure_ascii=False,
        )
        return await self._run(
            token,
            "POST",
            f"/api/v1/hr-cases/{case_id}/plan",
            {"goal": goal, "llm_proposal": proposal},
        )

    async def run(self, token: str, case_id: str):
        return await self._run(token, "POST", f"/api/v1/hr-cases/{case_id}/run")

    async def approve(self, token: str, case_id: str, approval_id: str, decision: str = "approve"):
        return await self._run(
            token,
            "POST",
            f"/api/v1/hr-cases/{case_id}/approve",
            {"approval_id": approval_id, "decision": decision, "reason": "acceptance"},
        )


async def _drive_case_to_awaiting_approval(http: _Http, token: str, title: str) -> tuple[str, str]:
    """Create a case, plan a write step, run until the approval is pending."""
    created = await http.create_case(token, subject_ref=f"EMP-{uuid4().hex[:6]}", category="overtime", title=title)
    assert created.status_code == 200, created.text
    case_id = created.json()["case_id"]

    planned = await http.plan(token, case_id, goal="复核加班费争议")
    assert planned.status_code == 200, planned.text

    run = await http.run(token, case_id)
    assert run.status_code == 200, run.text
    payload = run.json()
    approval_id = payload.get("approval_id")
    assert approval_id, f"run did not stop for approval: {payload}"
    return case_id, approval_id


@pytest.mark.asyncio
async def test_same_tenant_cross_user_case_access_fails_closed() -> None:
    """HRBP B must not read or mutate HRBP A's case over real HTTP."""
    tenant_id = str(uuid4())
    users = await _seed_users(tenant_id)
    try:
        async with _Http() as http:
            token_a = _token(tenant_id, users["hrbp_a"], "hrbp")
            token_b = _token(tenant_id, users["hrbp_b"], "hrbp")

            case_id, _approval = await _drive_case_to_awaiting_approval(http, token_a, "A的案例")

            read = await http.get_case(token_b, case_id)
            assert read.status_code == 404, f"cross-user read must 404, got {read.status_code}"

            # A mutation attempt on the same case id must also fail closed.
            plan_b = await http.plan(token_b, case_id, goal="越权计划")
            assert plan_b.status_code == 404, f"cross-user mutation must 404, got {plan_b.status_code}"
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_cross_case_approval_binding_fails_closed() -> None:
    """Approving Case A's path with Case B's approval id must 404, B stays pending."""
    tenant_id = str(uuid4())
    users = await _seed_users(tenant_id)
    try:
        async with _Http() as http:
            token_a = _token(tenant_id, users["hrbp_a"], "hrbp")
            # Approvals are decided by managers only (DECIDER_ROLES).
            token_m = _token(tenant_id, users["manager"], "hr_manager")

            case_a, _ = await _drive_case_to_awaiting_approval(http, token_a, "案例A")
            _case_b, approval_b = await _drive_case_to_awaiting_approval(http, token_a, "案例B")

            cross = await http.approve(token_m, case_a, approval_b)
            assert cross.status_code == 404, (
                f"case B approval consumed via case A path must 404, got {cross.status_code}"
            )

            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                status_b = await db.scalar(select(ApprovalRequest.status).where(ApprovalRequest.id == approval_b))
            assert status_b == "PENDING", f"case B's approval must stay PENDING, got {status_b}"
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_manager_scope_governs_case_access() -> None:
    """A manager reads cases created inside explicit org scope only."""
    tenant_id = str(uuid4())
    users = await _seed_users(tenant_id)
    try:
        async with _Http() as http:
            token_a = _token(tenant_id, users["hrbp_a"], "hrbp")
            token_b = _token(tenant_id, users["hrbp_b"], "hrbp")
            token_m = _token(tenant_id, users["manager"], "hr_manager")

            case_a, _ = await _drive_case_to_awaiting_approval(http, token_a, "范围内案例")
            case_b, _ = await _drive_case_to_awaiting_approval(http, token_b, "范围外案例")

            inside = await http.get_case(token_m, case_a)
            assert inside.status_code == 200, inside.text

            outside = await http.get_case(token_m, case_b)
            assert outside.status_code == 404, (
                f"manager must not read outside explicit org scope, got {outside.status_code}"
            )
    finally:
        await _cleanup(tenant_id)
