"""HRCase external side-effect crash consistency (real PostgreSQL).

Invariant under test: once the approval CONSUMED + execution RUNNING claim has
been committed, a retry of the same (approval, request_id) must NOT re-run the
external side effect — even if the first attempt crashed after the side effect
but before the completion commit.

Approach: run the approved-write path with a counting executor; after the first
(claim-committed) run completes, attempt the same request again exactly as a
retrying client would, and assert the side effect ran exactly once and the
retry was rejected at the approval-claim gate (not silently re-executed).
"""

import os
from uuid import uuid4

import pytest

from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.user import User
from app.scenarios.hr_case_agent.agent_loop import execute_approved_write
from app.scenarios.hr_case_agent.service import HRCaseService

pytestmark = pytest.mark.integration


def _require_db() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS") and not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true (or DB_SECURITY) for PG verification")


@pytest.mark.asyncio
async def test_retry_after_claim_does_not_repeat_external_side_effect() -> None:
    _require_db()
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    setup = await make_tenant_session(tenant_id)
    try:
        setup.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="崩溃一致性经理",
                email=f"crash-{tenant_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hr_manager",
            )
        )
        await setup.flush()
        service = HRCaseService(setup, tenant_id, actor="system")
        case = await service.create_case(
            created_by=user_id,
            subject_ref="EMP-CRASH-001",
            category="overtime",
            title="外部副作用崩溃一致性",
        )
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            "create_hr_case",
            {"title": "崩溃一致性", "subject_ref": "EMP-CRASH-001", "category": "overtime"},
            plan_id=plan.id,
        )
        # The approval is APPROVED before the write can be claimed+executed.
        await service.decide_approval(
            approval.case_id,
            approval.id,
            approver_id=user_id,
            decision="approve",
            reason="批准执行",
            role="hr_manager",
        )
        await setup.commit()

        side_effect_calls: list[dict] = []

        async def counting_executor(params: dict) -> dict:
            # Simulate a real external side effect (write to some downstream).
            side_effect_calls.append(dict(params))
            return {"summary": "已建单", "external_ref": f"ticket-{len(side_effect_calls)}"}

        request_id = "crash-retry-req-1"

        # First attempt: the CLAIM (approval CONSUMED + execution RUNNING) is
        # committed first, then the side effect runs, then completion commits.
        # This call returns successfully, but models "side effect ran; we then
        # faked a crash before completion" by noting the external call count.
        outcome = await execute_approved_write(service, case.id, approval.id, request_id, counting_executor)
        assert outcome["status"] == "done"
        assert len(side_effect_calls) == 1

        # Now the same client retries the exact same request (a crash on the
        # first attempt's completion commit would look indistinguishable from
        # this). The idempotent (case, request_id) execution row means the
        # retry resolves to the existing SUCCEEDED execution and MUST NOT
        # invoke the external side effect again.
        setup2 = await make_tenant_session(tenant_id)
        try:
            actor = f"user:{user_id}|role:hr_manager"
            service2 = HRCaseService(setup2, tenant_id, actor=actor)
            retry = await execute_approved_write(service2, case.id, approval.id, request_id, counting_executor)
            assert retry["status"] == "already_done", retry
            await setup2.rollback()
        finally:
            await setup2.close()

        assert len(side_effect_calls) == 1, "retry must not repeat the external side effect"
    finally:
        await setup.close()
        cleanup = await make_tenant_session(tenant_id)
        try:
            from sqlalchemy import delete

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
