"""Real PostgreSQL transaction coverage for the governed write gateway."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.runtime import ExecutionGrant, OutboxMessage
from app.data.models.user import User
from app.scenarios.hr_case_agent.service import HRCaseService
from app.tools.gateway import ToolGateway


@pytest.mark.asyncio
async def test_gateway_prepares_grant_execution_and_outbox_in_one_transaction() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL gateway verification")

    tenant_id, user_id = str(uuid4()), str(uuid4())
    session = await make_tenant_session(tenant_id)
    try:
        identity = (
            await session.execute(
                text("SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user")
            )
        ).one()
        assert identity == ("hrbp", False, False)
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="网关验证 HRBP",
                email=f"gateway-{tenant_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hrbp",
            )
        )
        await session.flush()
        service = HRCaseService(session, tenant_id, actor=f"user:{user_id}|role:hrbp", visible_user_ids={user_id})
        case = await service.create_case(user_id, "EMP-GATEWAY-001", "overtime", "网关事务验证")
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            "create_hr_case",
            {"title": "派发验证", "subject_ref": "EMP-GATEWAY-001", "category": "overtime"},
            plan_id=plan.id,
        )
        await service.decide_approval(case.id, approval.id, user_id, "approve", "verified", role="hr_manager")
        prepared = await ToolGateway(service).prepare_approved_write(case.id, approval.id, "gateway-request-1")
        await session.commit()

        assert prepared.execution_id
        assert prepared.deduplicated is False
        assert (
            await session.scalar(select(ApprovalRequest.status).where(ApprovalRequest.id == approval.id))
        ) == "CONSUMED"
        execution = await session.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
        assert execution is not None
        assert execution.status == "PENDING"
        assert execution.execution_grant_id == prepared.grant_id
        assert execution.outbox_message_id == prepared.outbox_id
        assert execution.dispatch_lease_owner is None
        assert execution.dispatch_lease_token == 0
        assert (
            await session.scalar(select(ExecutionGrant.id).where(ExecutionGrant.id == prepared.grant_id))
            == prepared.grant_id
        )
        assert (
            await session.scalar(select(OutboxMessage.id).where(OutboxMessage.id == prepared.outbox_id))
            == prepared.outbox_id
        )

        duplicate = await ToolGateway(service).prepare_approved_write(case.id, approval.id, "gateway-request-1")
        assert duplicate.execution_id == prepared.execution_id
        assert duplicate.grant_id == prepared.grant_id
        assert duplicate.outbox_id == prepared.outbox_id
        assert duplicate.deduplicated is True
    finally:
        await session.rollback()
        await session.execute(delete(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
        await session.execute(delete(OutboxMessage).where(OutboxMessage.tenant_id == tenant_id))
        await session.execute(delete(ExecutionGrant).where(ExecutionGrant.tenant_id == tenant_id))
        await session.execute(delete(CaseEvent).where(CaseEvent.tenant_id == tenant_id))
        await session.execute(delete(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id))
        await session.execute(delete(CasePlan).where(CasePlan.tenant_id == tenant_id))
        await session.execute(delete(AgentRun).where(AgentRun.tenant_id == tenant_id))
        await session.execute(delete(HRCase).where(HRCase.tenant_id == tenant_id))
        await session.execute(delete(User).where(User.tenant_id == tenant_id))
        await session.commit()
        await session.close()
