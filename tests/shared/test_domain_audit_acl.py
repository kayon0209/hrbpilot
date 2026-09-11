"""Object authorization and audit for manager decisions and publishing."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from starlette.requests import Request

from app.data.database import get_session_factory
from app.data.models.infra import AuditLog
from app.data.models.scenarios import KnowledgeFeedbackCandidate, WeeklyReport
from app.data.models.user import User


def _request(tenant_id: str, user_id: str, role: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    request.state.user_role = role
    return request


@pytest.mark.asyncio(loop_scope="module")
async def test_weekly_publish_is_owner_only_and_durably_audited():
    from app.access.routes.weekly_report import save_report
    from app.scenarios.weekly_report.schemas import SaveRequest
    from app.shared.errors import NotFoundError

    tenant_id, owner_id, other_id = str(uuid4()), str(uuid4()), str(uuid4())
    own_report, other_report = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    name="Owner",
                    email=f"{owner_id}@example.com",
                    hashed_password="x",
                    role="hrbp",
                ),
                User(
                    id=other_id,
                    tenant_id=tenant_id,
                    name="Other",
                    email=f"{other_id}@example.com",
                    hashed_password="x",
                    role="hrbp",
                ),
            ]
        )
        await db.flush()
        for report_id, created_by in ((own_report, owner_id), (other_report, other_id)):
            db.add(
                WeeklyReport(
                    id=report_id,
                    tenant_id=tenant_id,
                    created_by=created_by,
                    period="2026-W35",
                    summary="s",
                    progress_json="[]",
                    risks_json="[]",
                    plan_json="[]",
                    data_sources_json="[]",
                )
            )
        await db.commit()

    try:
        with pytest.raises(NotFoundError):
            await save_report(
                SaveRequest(report_id=other_report, action="publish"), _request(tenant_id, owner_id, "hrbp")
            )

        response = await save_report(
            SaveRequest(report_id=own_report, action="publish"), _request(tenant_id, owner_id, "hrbp")
        )
        assert response["status"] == "saved"

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            event = (
                (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.tenant_id == tenant_id,
                            AuditLog.scenario_id == "weekly_report.published",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert event.user_id == owner_id
        assert own_report in (event.input_summary or "")
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(delete(WeeklyReport).where(WeeklyReport.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio(loop_scope="module")
async def test_knowledge_decision_is_durably_audited():
    from app.scenarios.knowledge_feedback.service import DecideBody, decide_candidate

    tenant_id, manager_id, candidate_id = str(uuid4()), str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=manager_id,
                tenant_id=tenant_id,
                name="Manager",
                email=f"{manager_id}@example.com",
                hashed_password="x",
                role="hr_manager",
            )
        )
        await db.flush()
        db.add(
            KnowledgeFeedbackCandidate(
                id=candidate_id,
                tenant_id=tenant_id,
                source_user_id=manager_id,
                source_type="no_evidence",
                question="Q",
                occurrences=1,
                status="open",
            )
        )
        await db.commit()

    try:
        await decide_candidate(
            tenant_id,
            manager_id,
            "hr_manager",
            DecideBody(candidate_id=candidate_id, decision="confirm", reason="制度缺口"),
        )
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            event = (
                (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.tenant_id == tenant_id,
                            AuditLog.scenario_id == "knowledge_feedback.decided",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert event.user_id == manager_id
        assert candidate_id in (event.input_summary or "")
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(
                delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id)
            )
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()
