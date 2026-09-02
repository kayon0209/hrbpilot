"""HR Case API end-to-end tests (Phase 5).

Drives the real FastAPI app with an injected JWT over a faked DB layer? No —
uses the real app + real JWT auth and a REAL PostgreSQL (via DATABASE_URL
override when provided); otherwise exercises the request pipeline to prove
auth wiring, then skips DB-dependent flows.

The critical Phase 5 guarantees covered here at the HTTP boundary:
  - unauthenticated access is rejected
  - approve and execute are separate requests
  - execute requires hr_manager/admin
"""

import asyncio
import os
from datetime import UTC
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.user import User
from app.main import create_app

_JWT_ISSUER = "hrbp-ai-workbench"
_JWT_AUDIENCE = "hrbp-ai-workbench"


REAL_TENANT = "06c87e30-4abf-40ca-9805-3c8b44cc5fd5"
REAL_USERS = {
    "hr_manager": "721aa2ef-ab7e-47b7-9999-cb9bea4e0bf2",  # demo01@163.com seed user
    "employee": "10000000-0000-4000-8000-000000000001",
    "admin": "10000000-0000-4000-8000-000000000003",
}


def _make_token(role: str = "hr_manager", user_id: str | None = None, tenant_id: str = REAL_TENANT) -> str:
    user_id = user_id or REAL_USERS.get(role, REAL_USERS["hr_manager"])
    from datetime import datetime, timedelta

    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "email": f"{user_id}@example.com",
        "type": "access",
        "jti": "test-jti",
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": datetime.now(UTC),
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


@pytest.fixture()
def client():
    # One TestClient for the whole test = one event loop for all requests,
    # so the app's cached redis client and DB pool stay valid across calls.
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Rate limiter counts persist in Redis across tests/runs; the suite
    makes <10 requests but shares the seeded user with earlier runs. Clear
    the sliding windows up front (test-only)."""
    import asyncio

    import redis.asyncio as aioredis

    async def _flush():
        try:
            r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
            keys = await r.keys("ratelimit:*")
            if keys:
                await r.delete(*keys)
            await r.aclose()
        except Exception:
            pass  # redis unavailable: limiter fail-open covers it

    asyncio.run(_flush())


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_hr_case_endpoints_reject_anonymous(client):
    resp = client.post("/api/v1/hr-cases", json={"subject_ref": "S1", "category": "overtime", "title": "t"})
    assert resp.status_code in (401, 403)


@pytest.fixture()
def _real_db_required():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a real database via DATABASE_URL")


async def _seed_case_actor(tenant_id: str, user_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="HRCase HTTP test manager",
                email=f"{user_id}@example.test",
                hashed_password="not-used-by-test",
                role="hr_manager",
            )
        )
        await db.commit()


async def _cleanup_case_actor(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(CaseEvent).where(CaseEvent.tenant_id == tenant_id))
        await db.execute(delete(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
        await db.execute(delete(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id))
        await db.execute(delete(CasePlan).where(CasePlan.tenant_id == tenant_id))
        await db.execute(delete(AgentRun).where(AgentRun.tenant_id == tenant_id))
        await db.execute(delete(HRCase).where(HRCase.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


@pytest.fixture()
def _real_db_actor(_real_db_required):
    tenant_id, user_id = str(uuid4()), str(uuid4())
    asyncio.run(_seed_case_actor(tenant_id, user_id))
    try:
        yield tenant_id, user_id
    finally:
        asyncio.run(_cleanup_case_actor(tenant_id))


def test_full_case_flow_with_real_db(client, _real_db_actor):
    """Create → plan → run → approve → execute on a live database.

    This is the golden journey; it only runs when DATABASE_URL points at a
    disposable database (CI or local container).
    """
    tenant_id, user_id = _real_db_actor
    token = _make_token(user_id=user_id, tenant_id=tenant_id)
    headers = _auth_header(token)

    created = client.post(
        "/api/v1/hr-cases",
        json={"subject_ref": "EMP-SYN-777", "category": "overtime", "title": "加班费争议", "risk_level": "LOW"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["case_id"]

    planned = client.post(
        f"/api/v1/hr-cases/{case_id}/plan",
        json={"goal": "处理加班费争议"},
        headers=headers,
    )
    assert planned.status_code == 200, planned.text

    run = client.post(f"/api/v1/hr-cases/{case_id}/run", headers=headers)
    assert run.status_code == 200, run.text
    assert run.json()["status"] in {"AWAITING_APPROVAL", "COMPLETED", "HANDED_OFF"}

    if run.json()["status"] == "AWAITING_APPROVAL":
        approval_id = run.json()["approval_id"]
        approved = client.post(
            f"/api/v1/hr-cases/{case_id}/approve",
            json={"approval_id": approval_id, "decision": "approve", "reason": "同意"},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text

        executed = client.post(
            f"/api/v1/hr-cases/{case_id}/execute",
            json={"approval_id": approval_id, "request_id": "e2e-req-1"},
            headers=headers,
        )
        # No production executor registered for write tools in Phase 5 —
        # a 501 TOOL_EXECUTOR_MISSING is the honest outcome, not a fake 200.
        assert executed.status_code == 501
        assert executed.json()["code"] == "TOOL_EXECUTOR_MISSING"

    events = client.get(f"/api/v1/hr-cases/{case_id}/events", headers=headers)
    assert events.status_code == 200
    types = [e["type"] for e in events.json()["events"]]
    assert types[0] == "CASE_CREATED"


def test_execute_requires_manager_role(client, _real_db_required):
    # employee token hits the role gate first; the DB dependency resolves
    # before the handler runs, so this needs a reachable database.
    token = _make_token(role="employee")
    headers = _auth_header(token)
    resp = client.post(
        "/api/v1/hr-cases/some-case/execute",
        json={"approval_id": "a1", "request_id": "r1"},
        headers=headers,
    )
    assert resp.status_code == 403
