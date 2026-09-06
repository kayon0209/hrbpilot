"""HR Case API end-to-end tests (Phase 5).

Drives the real FastAPI app with an injected JWT over a faked DB layer? No —
uses the real app + real JWT auth and a REAL PostgreSQL (via DATABASE_URL
override when provided); otherwise exercises the request pipeline to prove
auth wiring, then skips DB-dependent flows.

The critical Phase 5 guarantees covered here at the HTTP boundary:
  - unauthenticated access is rejected
  - approve and execute are separate requests
  - execute requires an HR business role with case access
"""

import asyncio
import os
from datetime import UTC
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.notification import InAppNotification
from app.data.models.runtime import ExecutionGrant, OutboxMessage
from app.data.models.user import User
from app.data.models.work_task import WorkTask
from app.main import create_app
from app.outbox.worker import drain_tool_dispatches
from app.scenarios.hr_case_agent.service import HRCaseService

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


async def _seed_approved_write(
    tenant_id: str,
    user_id: str,
    *,
    tool_name: str = "create_work_task",
    params: dict | None = None,
) -> tuple[str, str]:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        service = HRCaseService(
            db,
            tenant_id,
            actor=f"user:{user_id}|role:hr_manager",
            visible_user_ids={user_id},
        )
        case = await service.create_case(user_id, "EMP-HTTP-DISPATCH", "overtime", "HTTP dispatch")
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            tool_name,
            params or {"title": "HTTP target", "next_action": "Collect attendance evidence"},
            plan_id=plan.id,
        )
        await service.decide_approval(case.id, approval.id, user_id, "approve", "verified", role="hr_manager")
        await db.commit()
        return case.id, approval.id
async def _cleanup_case_actor(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(InAppNotification).where(InAppNotification.tenant_id == tenant_id))
        await db.execute(delete(WorkTask).where(WorkTask.tenant_id == tenant_id))
        await db.execute(delete(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
        await db.execute(delete(OutboxMessage).where(OutboxMessage.tenant_id == tenant_id))
        await db.execute(delete(ExecutionGrant).where(ExecutionGrant.tenant_id == tenant_id))
        await db.execute(delete(CaseEvent).where(CaseEvent.tenant_id == tenant_id))
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
        assert executed.status_code == 202, executed.text
        accepted = executed.json()
        assert accepted["status"] == "accepted"
        assert accepted["deduplicated"] is False
        assert accepted["execution_id"]
        assert accepted["grant_id"]
        assert accepted["outbox_id"]

        duplicate = client.post(
            f"/api/v1/hr-cases/{case_id}/execute",
            json={"approval_id": approval_id, "request_id": "e2e-req-1"},
            headers=headers,
        )
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json() == {**accepted, "deduplicated": True}

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


def test_execute_endpoint_only_prepares_governed_dispatch(client, _real_db_actor):
    tenant_id, user_id = _real_db_actor
    case_id, approval_id = asyncio.run(_seed_approved_write(tenant_id, user_id))
    headers = _auth_header(_make_token(user_id=user_id, tenant_id=tenant_id))

    first = client.post(
        f"/api/v1/hr-cases/{case_id}/execute",
        json={"approval_id": approval_id, "request_id": "http-dispatch-1"},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    accepted = first.json()
    assert accepted["status"] == "accepted"
    assert accepted["deduplicated"] is False

    duplicate = client.post(
        f"/api/v1/hr-cases/{case_id}/execute",
        json={"approval_id": approval_id, "request_id": "http-dispatch-1"},
        headers=headers,
    )
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json() == {**accepted, "deduplicated": True}

    states = asyncio.run(
        drain_tool_dispatches(
            tenant_id,
            max_messages=10,
            worker_id="http-dispatch-worker",
        )
    )
    assert states == ["succeeded"]

    async def _load_task() -> WorkTask | None:
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            return await db.scalar(
                select(WorkTask).where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.idempotency_key == "http-dispatch-1",
                )
            )

    task = asyncio.run(_load_task())
    assert task is not None
    assert task.title == "HTTP target"
    assert task.next_action == "Collect attendance evidence"


def test_recipient_can_read_and_acknowledge_only_own_in_app_notification(client, _real_db_actor):
    tenant_id, user_id = _real_db_actor
    case_id, approval_id = asyncio.run(
        _seed_approved_write(
            tenant_id,
            user_id,
            tool_name="send_case_notification",
            params={
                "channel": "in_app",
                "recipient_ref": user_id,
                "template": "case_owner_action_required",
            },
        )
    )
    headers = _auth_header(_make_token(user_id=user_id, tenant_id=tenant_id))
    accepted = client.post(
        f"/api/v1/hr-cases/{case_id}/execute",
        json={"approval_id": approval_id, "request_id": "http-notification-1"},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    assert asyncio.run(drain_tool_dispatches(tenant_id, max_messages=10, worker_id="http-notification-worker")) == [
        "succeeded"
    ]

    inbox = client.get("/api/notifications", headers=headers)
    assert inbox.status_code == 200, inbox.text
    notifications = inbox.json()["notifications"]
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification["case_id"] == case_id
    assert notification["template"] == "case_owner_action_required"
    assert notification["read_at"] is None
    assert "params" not in notification

    read = client.post(f"/api/notifications/{notification['notification_id']}/read", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["read_at"] is not None

    other_user_id = str(uuid4())
    asyncio.run(_seed_case_actor(tenant_id, other_user_id))
    other_headers = _auth_header(_make_token(role="employee", user_id=other_user_id, tenant_id=tenant_id))
    assert client.get("/api/notifications", headers=other_headers).json() == {"notifications": []}
    assert (
        client.post(f"/api/notifications/{notification['notification_id']}/read", headers=other_headers).status_code
        == 404
    )
