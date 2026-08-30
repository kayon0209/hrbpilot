"""Regression tests for work-summary bucket semantics (audit 2026-08-31 P1-3/P1-5).

The continue card and the attention list used to carry the same object (the
continue pick came from the same status set attention filters on), so /tasks
rendered duplicated rows with duplicate React keys. Buckets must be mutually
exclusive, and genuinely urgent service work (open employee requests, pending
knowledge-feedback candidates) must reach the attention list.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete


@pytest.mark.asyncio
async def test_continue_work_and_attention_are_mutually_exclusive():
    from app.data.database import get_session_factory
    from app.data.models.scenarios import WeeklyReport
    from app.data.models.user import User
    from app.scenarios.work_summary.service import collect_work_summaries

    tenant_id = str(uuid4())
    actor_id = str(uuid4())
    report_id = str(uuid4())
    factory = get_session_factory()
    now = datetime.now(UTC)

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(User(id=actor_id, tenant_id=tenant_id, name="A", email=f"{actor_id}@example.com", hashed_password="x", role="hrbp"))
        await db.flush()
        db.add(
            WeeklyReport(
                id=report_id,
                tenant_id=tenant_id,
                period="2026-W36",
                summary="s",
                progress_json="[]",
                risks_json="[]",
                plan_json="[]",
                data_sources_json="[]",
                created_by=actor_id,
                updated_at=now,
            )
        )
        await db.commit()

    try:
        result = await collect_work_summaries(tenant_id, actor_id, "hrbp")
        ids = [item.work_id for item in result.attention]
        if result.continue_work is not None:
            assert result.continue_work.work_id not in ids, (
                "the continue card must not repeat under 需要你处理 (duplicate rows + duplicate React keys)"
            )
        assert len(ids) == len(set(ids)), "attention itself must not contain duplicates"
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(WeeklyReport).where(WeeklyReport.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_open_employee_requests_reach_the_attention_list():
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.scenarios.work_summary.service import collect_work_summaries

    tenant_id = str(uuid4())
    manager_id = str(uuid4())
    employee_id = str(uuid4())
    request_id = str(uuid4())
    org_id = str(uuid4())
    factory = get_session_factory()

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        from app.data.models.access_scope import ManagerOrgScope, OrgUnit

        db.add(OrgUnit(id=org_id, tenant_id=tenant_id, name="华东 HR 组"))
        await db.flush()
        db.add_all(
            [
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="M",
                    email=f"{manager_id}@example.com",
                    hashed_password="x",
                    role="hr_manager",
                    org_unit_id=org_id,
                ),
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="E",
                    email=f"{employee_id}@example.com",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_id,
                ),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_id))
        await db.flush()
        db.add(
            EmployeeRequest(
                id=request_id,
                tenant_id=tenant_id,
                created_by=employee_id,
                request_type="certificate",
                title="开具在职证明",
                description="需要收入证明",
                status="in_progress",
            )
        )
        await db.commit()

    try:
        result = await collect_work_summaries(tenant_id, manager_id, "hr_manager")
        ids = [item.work_id for item in result.attention]
        assert request_id in ids, "an open request inside the manager scope must appear in 需要你处理"

        # hrbp without assignment must NOT see the request (fail closed).
        other_hrbp = str(uuid4())
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(User(id=other_hrbp, tenant_id=tenant_id, name="H", email=f"{other_hrbp}@example.com", hashed_password="x", role="hrbp", org_unit_id=org_id))
            await db.commit()
        hrbp_view = await collect_work_summaries(tenant_id, other_hrbp, "hrbp")
        assert request_id not in [i.work_id for i in hrbp_view.attention]
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
            await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_open_knowledge_feedback_reaches_manager_attention():
    from app.data.database import get_session_factory
    from app.data.models.scenarios import KnowledgeFeedbackCandidate
    from app.scenarios.work_summary.service import collect_work_summaries

    tenant_id = str(uuid4())
    manager_id = str(uuid4())
    candidate_id = str(uuid4())
    factory = get_session_factory()

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            KnowledgeFeedbackCandidate(
                id=candidate_id,
                tenant_id=tenant_id,
                source_type="no_evidence",
                question="试用期多久？",
                occurrences=2,
                status="open",
            )
        )
        await db.commit()

    try:
        result = await collect_work_summaries(tenant_id, manager_id, "hr_manager")
        assert candidate_id in [item.work_id for item in result.attention], (
            "an open manager-judgment candidate must appear in 需要你处理"
        )
        # Non-manager roles never see governance candidates.
        hrbp_result = await collect_work_summaries(tenant_id, manager_id, "hrbp")
        assert candidate_id not in [item.work_id for item in hrbp_result.attention]
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id))
            await db.commit()
