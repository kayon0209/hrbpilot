"""PostgreSQL regression coverage for single-owner approval decisions.

Invariants under test:
- approve vs reject racing on the same PENDING approval: exactly one wins, the
  other gets 409;
- approve vs expire: approving after expiry must be rejected and the approval
  must be durably EXPIRED (not rolled back);
- a second decide on an already-decided approval is rejected.

These run against the isolated, disposable PostgreSQL database ("no mocks"),
so the atomic UPDATE ... WHERE status=PENDING guard is genuinely exercised.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.user import User
from app.scenarios.hr_case_agent.service import ApprovalError, HRCaseService

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")


async def _make_pending_approval(ttl_seconds: int = 3600) -> tuple[str, str, str, str]:
    """Return (tenant_id, user_id, case_id, approval_id) with one PENDING approval."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    setup = await make_tenant_session(tenant_id)
    try:
        setup.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="决策竞态经理",
                email=f"decide-race-{tenant_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hr_manager",
            )
        )
        await setup.flush()
        service = HRCaseService(setup, tenant_id, actor="system")
        case = await service.create_case(
            created_by=user_id,
            subject_ref="EMP-DECIDE-RACE",
            category="overtime",
            title="审批决策竞态回归",
        )
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            "create_hr_case",
            {"title": "竞态回归", "subject_ref": "EMP-DECIDE-RACE", "category": "overtime"},
            plan_id=plan.id,
            ttl_seconds=ttl_seconds,
        )
        await setup.commit()
        return (tenant_id, user_id, case.id, approval.id)
    except Exception:
        await setup.close()
        raise


async def _cleanup(tenant_id: str) -> None:
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


async def test_approve_vs_reject_exactly_one_wins() -> None:
    _require()
    tenant_id, user_id, case_id, approval_id = await _make_pending_approval()

    async def decide(decision: str) -> str:
        """Decide from an isolated session like two separate HTTP requests."""
        session = await make_tenant_session(tenant_id)
        try:
            actor = f"user:{user_id}|role:hr_manager"
            service = HRCaseService(session, tenant_id, actor=actor)
            ap = await service.decide_approval(
                case_id,
                approval_id,
                approver_id=user_id,
                decision=decision,
                reason="concurrent",
                role="hr_manager",
            )
            await session.commit()
            return ap.status
        except ApprovalError as exc:
            await session.rollback()
            return f"409:{exc.status_code}"
        finally:
            await session.close()

    outcomes = await asyncio.gather(decide("approve"), decide("reject"), decide("approve"), return_exceptions=True)
    winners = [o for o in outcomes if o in ("APPROVED", "REJECTED")]
    losers = [o for o in outcomes if str(o).startswith("409")]
    assert len(winners) == 1, f"expected exactly one winner, got {outcomes}"
    assert len(losers) == 2, f"expected two losers, got {outcomes}"

    verify = await make_tenant_session(tenant_id)
    try:
        row = (
            (
                await verify.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id
                    )
                )
            )
            .scalars()
            .first()
        )
        assert row.status in ("APPROVED", "REJECTED")
    finally:
        await verify.close()
    await _cleanup(tenant_id)


async def test_approve_after_expiry_is_rejected_and_durably_expired() -> None:
    """A decision that arrives too late must not win; the approval becomes
    durably EXPIRED even though the decide call raises."""
    _require()
    tenant_id, user_id, case_id, approval_id = await _make_pending_approval(ttl_seconds=1)

    # Let the approval expire.
    await asyncio.sleep(2)

    async def decide() -> str:
        session = await make_tenant_session(tenant_id)
        try:
            actor = f"user:{user_id}|role:hr_manager"
            service = HRCaseService(session, tenant_id, actor=actor)
            await service.decide_approval(
                case_id,
                approval_id,
                approver_id=user_id,
                decision="approve",
                reason="too late",
                role="hr_manager",
            )
            await session.commit()
            return "APPROVED"
        except ApprovalError as exc:
            await session.rollback()
            return f"409:{exc.status_code}"
        finally:
            await session.close()

    result = await decide()
    assert result.startswith("409"), f"expired approval must reject approval, got {result}"

    verify = await make_tenant_session(tenant_id)
    try:
        row = (
            (
                await verify.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id
                    )
                )
            )
            .scalars()
            .first()
        )
        assert row.status == "EXPIRED", f"expiration must be persisted, got {row.status}"
    finally:
        await verify.close()
    await _cleanup(tenant_id)


async def test_repeat_decision_on_decided_approval_is_rejected() -> None:
    _require()
    tenant_id, user_id, case_id, approval_id = await _make_pending_approval()

    session = await make_tenant_session(tenant_id)
    try:
        actor = f"user:{user_id}|role:hr_manager"
        service = HRCaseService(session, tenant_id, actor=actor)
        ap = await service.decide_approval(
            case_id,
            approval_id,
            approver_id=user_id,
            decision="approve",
            reason="first",
            role="hr_manager",
        )
        await session.commit()
        assert ap.status == "APPROVED"
    finally:
        await session.close()

    # A second request against the now-APPROVED approval must be a 409, no way
    # to flip it to REJECTED.
    session2 = await make_tenant_session(tenant_id)
    try:
        actor = f"user:{user_id}|role:hr_manager"
        service = HRCaseService(session2, tenant_id, actor=actor)
        with pytest.raises(ApprovalError) as exc_info:
            await service.decide_approval(
                case_id,
                approval_id,
                approver_id=user_id,
                decision="reject",
                reason="second",
                role="hr_manager",
            )
        assert exc_info.value.status_code == 409

        verify = await make_tenant_session(tenant_id)
        try:
            row = (
                (
                    await verify.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert row.status == "APPROVED", "a decided approval must not be re-decided"
        finally:
            await verify.close()
    finally:
        await session2.close()
    await _cleanup(tenant_id)
