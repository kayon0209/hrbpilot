"""PostgreSQL regression coverage for one-time approval consumption."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.user import User
from app.scenarios.hr_case_agent.service import ApprovalError, HRCaseService


@pytest.mark.asyncio
async def test_concurrent_requests_consume_an_approval_once() -> None:
    """Removing the atomic approval claim lets two request IDs start two writes."""
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    tenant_id = str(uuid4())
    user_id = str(uuid4())
    setup = await make_tenant_session(tenant_id)
    approval_id: str | None = None

    try:
        setup.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="并发测试经理",
                email=f"concurrency-{tenant_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hr_manager",
            )
        )
        await setup.flush()
        service = HRCaseService(setup, tenant_id, actor="system")
        case = await service.create_case(
            created_by=user_id,
            subject_ref="EMP-CONCURRENCY-001",
            category="overtime",
            title="审批消费并发回归",
        )
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            "create_hr_case",
            {"title": "并发回归", "subject_ref": "EMP-CONCURRENCY-001", "category": "overtime"},
            plan_id=plan.id,
        )
        await service.decide_approval(case.id, approval.id, user_id, "approve", "test", role="hr_manager")
        approval_id = approval.id
        await setup.commit()

        barrier = asyncio.Barrier(2)

        async def consume(request_id: str) -> str:
            session = await make_tenant_session(tenant_id)
            try:
                service = HRCaseService(session, tenant_id, actor="system")
                await barrier.wait()
                execution = await service.begin_tool_execution(
                    case.id,
                    "create_hr_case",
                    {"title": "并发回归", "subject_ref": "EMP-CONCURRENCY-001", "category": "overtime"},
                    request_id=request_id,
                    approval_id=approval_id,
                )
                await session.commit()
                return execution.id
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

        outcomes = await asyncio.gather(consume("concurrent-a"), consume("concurrent-b"), return_exceptions=True)
        success_count = sum(isinstance(outcome, str) for outcome in outcomes)
        assert success_count == 1
        assert any(isinstance(outcome, ApprovalError) for outcome in outcomes)

        verify = await make_tenant_session(tenant_id)
        try:
            executions = (
                (
                    await verify.execute(
                        select(ToolExecution).where(
                            ToolExecution.tenant_id == tenant_id,
                            ToolExecution.approval_id == approval_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(executions) == 1
        finally:
            await verify.close()
    finally:
        await setup.close()
        cleanup = await make_tenant_session(tenant_id)
        try:
            await cleanup.execute(delete(CaseEvent).where(CaseEvent.tenant_id == tenant_id))
            await cleanup.execute(delete(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
            await cleanup.execute(delete(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id))
            await cleanup.execute(delete(CasePlan).where(CasePlan.tenant_id == tenant_id))
            await cleanup.execute(delete(AgentRun).where(AgentRun.tenant_id == tenant_id))
            await cleanup.execute(delete(HRCase).where(HRCase.tenant_id == tenant_id))
            await cleanup.execute(delete(User).where(User.tenant_id == tenant_id))
            await cleanup.commit()
        finally:
            await cleanup.close()
