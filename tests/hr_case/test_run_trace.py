"""Agent run trace observability tests (Phase 7).

The run trace endpoint must expose plan, tool executions, approvals and
events for one run — everything needed to reconstruct what happened.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models import hr_case
from app.data.models.base import Base
from app.scenarios.hr_case_agent import agent_loop
from app.scenarios.hr_case_agent.service import HRCaseService

H = hr_case  # short alias used in the table list below


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import asyncio

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c,
                    tables=[
                        H.HRCase.__table__,
                        H.CasePlan.__table__,
                        H.ApprovalRequest.__table__,
                        H.ToolExecution.__table__,
                        H.CaseEvent.__table__,
                        H.AgentRun.__table__,
                    ],
                )
            )

    asyncio.run(make())
    yield factory
    import asyncio

    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_executors():
    yield
    agent_loop.TOOL_EXECUTORS.clear()


async def test_run_trace_exposes_full_trajectory(session_factory, monkeypatch):
    from starlette.requests import Request

    from app.access.routes import hr_case as routes

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="agent")
        case = await service.create_case("u1", "S1", "overtime", "加班费争议")
        await service.transition_case(case.id, "TRIAGED")
        run = await service.start_agent_run(case.id, "查询加班费制度")
        await session.commit()

        async def exec_fn(params: dict) -> dict:
            return {"summary": "found 3 chunks"}

        agent_loop.register_tool_executor("search_policy", exec_fn)
        import json

        from app.scenarios.hr_case_agent.planner import Planner

        draft = Planner(None).validate(
            json.dumps({"steps": [{"tool": "search_policy", "params": {"query": "加班费"}}]})
        )
        await service.save_plan(
            case.id,
            steps=[
                {"tool": s.tool, "params": s.params, "reason": s.reason, "expected": s.expected} for s in draft.steps
            ],
            rationale=draft.rationale,
            agent_run_id=run.id,
        )
        outcome = await agent_loop.run_plan(service, case.id, draft, agent_run_id=run.id)
        await session.commit()
        assert outcome.status == "COMPLETED"

        # call the trace endpoint with a faked auth context
        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)})
        request.state.user_id = "u1"
        request.state.tenant_id = "t1"
        request.state.user_role = "hr_manager"

        result = await routes.get_agent_run_trace(case.id, run.id, request, session)

        assert result["run"]["id"] == run.id
        assert result["run"]["status"] == "COMPLETED"
        assert result["plan"]["steps"][0]["tool"] == "search_policy"
        assert result["tool_executions"] == []  # read tools don't create execution rows
        assert any(e["type"] == "STATUS_CHANGED" for e in result["events"])
