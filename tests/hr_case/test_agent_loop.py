"""Bounded HR Case Agent tests (Phase 5).

Covers the hard guarantees: unapproved writes never execute, duplicate
requests cause zero extra side effects, budgets stop runaway runs, tool
failures retry once then hand off, and plans are validated server-side.
"""

import json
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models.base import Base
from app.data.models.hr_case import ToolExecution
from app.scenarios.hr_case_agent import agent_loop
from app.scenarios.hr_case_agent.agent_loop import execute_approved_write, register_tool_executor, run_plan
from app.scenarios.hr_case_agent.planner import MAX_PLAN_STEPS, Planner, PlanValidationError, requires_human_review
from app.scenarios.hr_case_agent.service import ApprovalError, HRCaseService
from app.scenarios.hr_case_agent.tools import ToolError, validate_tool_call


@pytest.fixture()
def engine():
    return create_async_engine("sqlite+aiosqlite://")


@pytest.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(engine):
    from app.data.models import hr_case

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
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


@pytest.fixture(autouse=True)
def _clean_executors():
    yield
    agent_loop.TOOL_EXECUTORS.clear()


async def make_case(session_factory, risk="LOW", category="overtime"):
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        case = await service.create_case("u1", "EMP-SYN-001", category, "加班费争议", risk_level=risk)
        await session.commit()
        return case.id


def ok_executor(summary="done") -> Callable[[dict], Awaitable[dict]]:
    async def _exec(params: dict) -> dict:
        return {"summary": summary, "params": params}

    return _exec


# --- planner validation ---


def test_planner_accepts_valid_read_only_plan():
    planner = Planner(lambda ctx: None)
    plan = planner.validate(json.dumps({"steps": [{"tool": "search_policy", "params": {"query": "加班费"}}]}))
    assert plan.steps[0].tool == "search_policy"


def test_planner_rejects_unknown_tool():
    planner = Planner(lambda ctx: None)
    with pytest.raises(PlanValidationError, match="UNKNOWN_TOOL"):
        planner.validate(json.dumps({"steps": [{"tool": "drop_database", "params": {}}]}))


def test_planner_rejects_more_than_max_steps():
    planner = Planner(lambda ctx: None)
    steps = [{"tool": "search_policy", "params": {"query": "q"}} for _ in range(MAX_PLAN_STEPS + 1)]
    with pytest.raises(PlanValidationError, match="max is"):
        planner.validate(json.dumps({"steps": steps}))


def test_planner_rejects_two_writes():
    planner = Planner(lambda ctx: None)
    steps = [
        {"tool": "create_hr_case", "params": {"title": "t", "subject_ref": "s", "category": "c"}},
        {"tool": "assign_case_owner", "params": {"owner_id": "u1"}},
    ]
    with pytest.raises(PlanValidationError, match="one write"):
        planner.validate(json.dumps({"steps": steps}))


def test_planner_rejects_write_in_middle():
    planner = Planner(lambda ctx: None)
    steps = [
        {"tool": "create_hr_case", "params": {"title": "t", "subject_ref": "s", "category": "c"}},
        {"tool": "search_policy", "params": {"query": "q"}},
    ]
    with pytest.raises(PlanValidationError, match="final step"):
        planner.validate(json.dumps({"steps": steps}))


def test_planner_rejects_non_json_and_missing_steps():
    planner = Planner(lambda ctx: None)
    with pytest.raises(PlanValidationError):
        planner.validate("not json")
    with pytest.raises(PlanValidationError):
        planner.validate(json.dumps({"rationale": "no steps"}))


def test_high_risk_requires_human_review():
    assert requires_human_review("termination", "LOW")
    assert requires_human_review("overtime", "HIGH")
    assert not requires_human_review("overtime", "LOW")


def test_tool_schema_validation_rejects_bad_params():
    with pytest.raises(ToolError, match="INVALID_PARAMS"):
        validate_tool_call("create_hr_case", {"title": ""})
    normalized = validate_tool_call("search_policy", {"query": "加班", "top_k": "3"})
    assert normalized["top_k"] == 3  # coerced


# --- agent loop ---


async def test_read_only_plan_completes(session_factory):
    case_id = await make_case(session_factory)
    register_tool_executor("search_policy", ok_executor("found 3 chunks"))
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        run = await service.start_agent_run(case_id, "查制度")
        await session.commit()

        planner = Planner(None)
        plan = planner.validate(json.dumps({"steps": [{"tool": "search_policy", "params": {"query": "加班费"}}]}))
        result = await run_plan(service, case_id, plan, agent_run_id=run.id, start_status="TRIAGED")
        assert result.status == "COMPLETED"
        assert result.steps_taken == 1


async def test_write_tool_stops_for_approval_and_never_executes(session_factory):
    case_id = await make_case(session_factory)
    executed: list[dict] = []

    async def spy_exec(params: dict) -> dict:
        executed.append(params)
        return {"summary": "should not run"}

    register_tool_executor("search_policy", ok_executor("found chunks"))
    register_tool_executor("create_hr_case", spy_exec)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        run = await service.start_agent_run(case_id, "建单")
        await session.commit()

        planner = Planner(None)
        plan = planner.validate(
            json.dumps(
                {
                    "steps": [
                        {"tool": "search_policy", "params": {"query": "加班费标准"}},
                        {"tool": "create_hr_case", "params": {"title": "加班费争议", "subject_ref": "EMP-SYN-001", "category": "overtime"}},
                    ]
                }
            )
        )
        result = await run_plan(service, case_id, plan, agent_run_id=run.id)
        assert result.status == "AWAITING_APPROVAL"
        assert result.approval_id is not None
        assert executed == []  # NOTHING ran without approval


async def test_approved_write_executes_exactly_once(session_factory):
    case_id = await make_case(session_factory)
    calls: list[dict] = []

    async def spy_exec(params: dict) -> dict:
        calls.append(params)
        return {"summary": "case created"}

    register_tool_executor("create_hr_case", spy_exec)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "加班费争议", "subject_ref": "EMP-SYN-001", "category": "overtime"}, plan_id=plan.id
        )
        await service.decide_approval(approval.id, "u9", "approve", "同意", role="hr_manager")
        await session.commit()

        async def executor(params: dict) -> dict:
            return await spy_exec(params)

        first = await execute_approved_write(service, case_id, approval.id, "req-x", executor)
        # duplicate request id → idempotent, no second side effect
        second = await execute_approved_write(service, case_id, approval.id, "req-x", executor)

        assert first["status"] == "done"
        assert second["status"] == "already_done"
        assert len(calls) == 1
        # service committed; verify through the same session instead of a new one
        rows = (
            await session.execute(
                ToolExecution.__table__.select().where(ToolExecution.__table__.c.request_id == "req-x")
            )
        ).all()
        assert len(rows) == 1


async def test_unapproved_write_execution_is_blocked(session_factory):
    case_id = await make_case(session_factory)
    register_tool_executor("create_hr_case", ok_executor())
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        await service.transition_case(case_id, "EVIDENCE_READY")
        plan = await service.save_plan(case_id, steps=[])
        approval = await service.request_approval(
            case_id, "create_hr_case", {"title": "t", "subject_ref": "s", "category": "overtime"}, plan_id=plan.id
        )
        # deliberately NOT approving
        with pytest.raises(ApprovalError):
            await execute_approved_write(service, case_id, approval.id, "req-y", ok_executor())


async def test_tool_failure_retries_once_then_hands_off(session_factory):
    case_id = await make_case(session_factory)
    attempts: list[int] = []

    async def flaky(params: dict) -> dict:
        attempts.append(1)
        if len(attempts) < 2:
            raise ToolError("TIMEOUT", "upstream timed out")
        return {"summary": "recovered"}

    register_tool_executor("search_policy", flaky)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        run = await service.start_agent_run(case_id, "查")
        await session.commit()
        plan = Planner(None).validate(json.dumps({"steps": [{"tool": "search_policy", "params": {"query": "q"}}]}))
        result = await run_plan(service, case_id, plan, agent_run_id=run.id)
        assert result.status == "COMPLETED"
        assert len(attempts) == 2  # 1 failure + 1 retry


async def test_tool_failure_after_retry_hands_off(session_factory):
    case_id = await make_case(session_factory)
    attempts: list[int] = []

    async def dead(params: dict) -> dict:
        attempts.append(1)
        raise ToolError("TIMEOUT", "down")

    register_tool_executor("search_policy", dead)
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.transition_case(case_id, "TRIAGED")
        run = await service.start_agent_run(case_id, "查")
        await session.commit()
        plan = Planner(None).validate(json.dumps({"steps": [{"tool": "search_policy", "params": {"query": "q"}}]}))
        result = await run_plan(service, case_id, plan, agent_run_id=run.id)
        assert result.status == "HANDED_OFF"
        assert "retry" in (result.handoff_reason or "")
        assert len(attempts) == 2  # capped at 1 retry
