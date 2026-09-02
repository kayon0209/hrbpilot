"""HRCASE-01: per-case event seq is unique under concurrent appends."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.user import User
from app.scenarios.hr_case_agent.service import HRCaseService

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")


@pytest.mark.asyncio
async def test_concurrent_event_appends_get_distinct_monotonic_seqs() -> None:
    _require()
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    setup = await make_tenant_session(tenant_id)
    case_id = ""
    try:
        setup.add(
            User(id=user_id, tenant_id=tenant_id, name="seq经理", email=f"seq-{tenant_id}@example.invalid",
                 hashed_password="x", role="hr_manager")
        )
        await setup.flush()
        service = HRCaseService(setup, tenant_id, actor="system")
        case = await service.create_case(
            created_by=user_id, subject_ref="EMP-SEQ-1", category="overtime", title="并发事件序号"
        )
        case_id = case.id
        await setup.commit()

        async def append_one(n: int) -> int:
            session = await make_tenant_session(tenant_id)
            try:
                svc = HRCaseService(session, tenant_id, actor=f"agent-{n}")
                await svc._append_event(case_id, "CONCURRENT_TEST", {"n": n})
                await session.commit()
                return n
            finally:
                await session.close()

        await asyncio.gather(*[append_one(i) for i in range(20)])

        verify = await make_tenant_session(tenant_id)
        try:
            seqs = (
                await verify.execute(
                    select(CaseEvent.seq).where(CaseEvent.case_id == case_id)
                )
            ).scalars().all()
            assert len(seqs) == len(set(seqs)), "duplicate seq under concurrency"
            assert sorted(seqs) == list(range(1, len(seqs) + 1))
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
