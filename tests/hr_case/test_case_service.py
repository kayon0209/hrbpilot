"""HRCaseService tenant-safety and lifecycle tests (Phase 4).

Uses an in-memory SQLite via aiosqlite when available; the service logic
under test (state machine gating, tenant scoping, idempotency, approvals) is
storage-agnostic. RLS itself is exercised by integration tests against PG.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models.base import Base
from app.data.models.hr_case import ToolExecution
from app.scenarios.hr_case_agent.service import (
    ApprovalError,
    CasePermissionDeniedError,
    HRCaseService,
)
from app.scenarios.hr_case_agent.state import InvalidTransitionError
from app.shared.errors import NotFoundError


@pytest.fixture()
def engine():
    try:
        import aiosqlite  # noqa: F401
    except ImportError:
        pytest.skip("aiosqlite not installed")
    return create_async_engine("sqlite+aiosqlite://")


@pytest.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(engine, session_factory):
    # Only create the tables this service touches — the full metadata
    # includes PG-specific TSVECTOR columns SQLite can't render.
    from app.data.models import hr_case

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    hr_case.HRCase.__table__,
                    hr_case.CasePlan.__table__,
                    hr_case.ApprovalRequest.__table__,
                    hr_case.ToolExecution.__table__,
                    hr_case.CaseEvent.__table__,
                    hr_case.AgentRun.__table__,
                ],
            )
        )


async def _service(session_factory, tenant_id="t1", actor="user:u1"):
    async with session_factory() as session:
        yield HRCaseService(session, tenant_id, actor)


@pytest.fixture()
def svc(session_factory):
    return _service(session_factory)


async def make_case(session_factory, tenant="t1", **overrides):
    async with session_factory() as session:
        service = HRCaseService(session, tenant)
        case = await service.create_case(
            created_by=overrides.get("created_by", "u1"),
            subject_ref=overrides.get("subject_ref", "EMP-SYN-001"),
            category=overrides.get("category", "overtime"),
            title=overrides.get("title", "加班费争议"),
            risk_level=overrides.get("risk_level", "HIGH"),
        )
        await session.commit()
        return case.id


async def test_create_case_writes_creation_event(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        events = await service.list_events(case_id)
        assert [e.event_type for e in events] == ["CASE_CREATED"]
        assert events[0].seq == 1


async def test_full_lifecycle_through_service(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="agent")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[{"tool": "create_hr_case", "params": {}}])
        assert plan.steps_json and json.loads(plan.steps_json)[0]["tool"] == "create_hr_case"

        approval = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params={"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
            plan_id=plan.id,
        )
        decided = await service.decide_approval(
            case_id, approval.id, approver_id="u9", decision="approve", reason="ok", role="hr_manager"
        )
        assert decided.status == "APPROVED"

        execution = await service.begin_tool_execution(
            case_id,
                "create_hr_case",
                {"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
                request_id="req-1",
                approval_id=approval.id,
        )
        await service.finish_tool_execution(execution.id, ok=True, result_summary="case created")
        events = await service.list_events(case_id)
        assert [e.event_type for e in events][-2:] == ["TOOL_EXECUTION_STARTED", "TOOL_EXECUTION_FINISHED"]
        assert [e.seq for e in events] == sorted(e.seq for e in events)


async def test_cross_tenant_access_is_not_found(session_factory):
    case_id = await make_case(session_factory, tenant="tenant-a")
    async with session_factory() as session:
        other = HRCaseService(session, "tenant-b")
        with pytest.raises(NotFoundError):
            await other.get_case(case_id)
        with pytest.raises(NotFoundError):
            await other.list_events(case_id)


async def test_same_tenant_hrbp_cannot_load_another_users_case(session_factory):
    """Removing the creator-scope predicate would expose a peer's HR case."""
    case_id = await make_case(session_factory, created_by="u1")

    async with session_factory() as session:
        peer = HRCaseService(session, "t1", actor="user:u2|role:hrbp")

        with pytest.raises(NotFoundError):
            await peer.get_case(case_id)


async def test_approval_decision_rejects_an_approval_from_another_case(session_factory):
    """Removing the path-case binding would let Case A decide Case B's approval."""
    case_a_id = await make_case(session_factory, created_by="u1")
    case_b_id = await make_case(session_factory, created_by="u1")

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hr_manager")
        await service.transition_case(case_b_id, "TRIAGED")
        await service.transition_case(case_b_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_b_id, steps=[])
        approval = await service.request_approval(
            case_b_id,
            "create_hr_case",
            {"title": "x", "subject_ref": "S1", "category": "overtime"},
            plan_id=plan.id,
        )

        with pytest.raises(NotFoundError):
            await service.decide_approval(
                case_a_id,
                approval.id,
                approver_id="u1",
                decision="approve",
                reason=None,
                role="hr_manager",
            )

        assert approval.status == "PENDING"


async def test_transition_rejected_by_state_machine(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        with pytest.raises(InvalidTransitionError):
            await service.transition_case(case_id, "EXECUTING")


async def test_duplicate_request_id_is_idempotent(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[{"tool": "update_case_status", "params": {"status": "RESOLVED"}}])
        approval = await service.request_approval(
            case_id, tool_name="update_case_status", params={"status": "RESOLVED"}, plan_id=plan.id
        )
        await service.decide_approval(case_id, approval.id, "u9", "approve", None, role="admin")

        first = await service.begin_tool_execution(
            case_id, "update_case_status", {"status": "RESOLVED"}, request_id="req-dup", approval_id=approval.id
        )
        await service.finish_tool_execution(first.id, ok=True, result_summary="done")
        # same request_id after SUCCESS → returns the SUCCEEDED row, no new side effect
        second = await service.begin_tool_execution(
            case_id, "update_case_status", {"status": "RESOLVED"}, request_id="req-dup", approval_id=approval.id
        )
        assert first.id == second.id
        rows = (
            await session.execute(select(ToolExecution).where(ToolExecution.request_id == "req-dup"))
        ).scalars().all()
        assert len(rows) == 1


async def test_write_tool_without_approval_is_rejected(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        await service.save_plan(case_id, steps=[{"tool": "create_hr_case", "params": {}}])
        with pytest.raises(ApprovalError):
            await service.begin_tool_execution(
                case_id, "create_hr_case", {"title": "x"}, request_id="req-2"
            )


async def test_rejected_approval_cannot_execute(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "x", "subject_ref": "S1", "category": "overtime"}, plan_id=plan.id
        )
        await service.decide_approval(case_id, approval.id, "u9", "reject", "不需要建单", role="hr_manager")
        with pytest.raises(ApprovalError):
            await service.begin_tool_execution(
                case_id, "create_hr_case", {"title": "x"}, request_id="req-3", approval_id=approval.id
            )


async def test_expired_approval_rejected(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "x", "subject_ref": "S1", "category": "overtime"}, plan_id=plan.id, ttl_seconds=-1
        )
        with pytest.raises(ApprovalError):
            await service.decide_approval(case_id, approval.id, "u9", "approve", None, role="admin")


async def test_employee_cannot_decide_approvals(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "x", "subject_ref": "S1", "category": "overtime"}, plan_id=plan.id
        )
        with pytest.raises(CasePermissionDeniedError):
            await service.decide_approval(case_id, approval.id, "u2", "approve", None, role="employee")


async def test_params_mismatch_between_approval_and_execution(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "真标题", "subject_ref": "S1", "category": "overtime"}, plan_id=plan.id
        )
        await service.decide_approval(case_id, approval.id, "u9", "approve", None, role="hr_manager")
        with pytest.raises(ApprovalError):
            await service.begin_tool_execution(
                case_id,
                "create_hr_case",
                {"title": "换掉的标题", "subject_ref": "S1", "category": "overtime"},
                request_id="req-4",
                approval_id=approval.id,
            )


async def test_plan_auto_advances_from_new(session_factory):
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        plan = await service.save_plan(case_id, steps=[])
        case = await service.get_case(case_id)
        assert case.status == "PLAN_READY"
        assert plan.steps_json


async def test_events_are_monotonic_per_case(session_factory):
    id1 = await make_case(session_factory, tenant="t1")
    id2 = await make_case(session_factory, tenant="t1")
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        # drive id1 through clarification loop to generate several events
        await service.transition_case(id1, "TRIAGED")
        await service.transition_case(id1, "NEEDS_CLARIFICATION")
        await service.transition_case(id1, "TRIAGED")
        await service.transition_case(id2, "TRIAGED")
        events1 = await service.list_events(id1)
        events2 = await service.list_events(id2)
        # CASE_CREATED + 3 transitions for id1; CASE_CREATED + 1 for id2
        assert [e.seq for e in events1] == [1, 2, 3, 4]
        assert [e.seq for e in events2] == [1, 2]


async def test_failed_execution_cannot_rerun_under_consumed_approval(session_factory):
    """Phase 7 demo finding: a FAILED execution must not silently re-run under
    its consumed approval — retry requires a fresh approval + request_id."""
    case_id = await make_case(session_factory)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[{"tool": "update_case_status", "params": {"status": "RESOLVED"}}])
        approval = await service.request_approval(
            case_id, tool_name="update_case_status", params={"status": "RESOLVED"}, plan_id=plan.id
        )
        await service.decide_approval(case_id, approval.id, "u9", "approve", None, role="admin")

        first = await service.begin_tool_execution(
            case_id, "update_case_status", {"status": "RESOLVED"}, request_id="req-f", approval_id=approval.id
        )
        await service.finish_tool_execution(first.id, ok=False, error_code="PROVIDER_TIMEOUT")

        with pytest.raises(ApprovalError):
            await service.begin_tool_execution(
                case_id, "update_case_status", {"status": "RESOLVED"}, request_id="req-f", approval_id=approval.id
            )
