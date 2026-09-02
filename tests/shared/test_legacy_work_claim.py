"""Legacy ownerless work must be visible to admins and claimed explicitly."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask, AuditLog
from app.data.models.scenarios import CultureContent, KnowledgeFeedbackCandidate, WeeklyReport
from app.data.models.user import User
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(tenant_id: str, admin_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": admin_id,
                "role": "admin",
                "tenant_id": tenant_id,
                "email": f"{admin_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


async def _seed(tenant_id: str) -> tuple[str, str, str, str]:
    admin_id, hrbp_id = str(uuid4()), str(uuid4())
    task_id, report_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=admin_id,
                    tenant_id=tenant_id,
                    name="Admin",
                    email=f"{admin_id}@example.test",
                    hashed_password="x",
                    role="admin",
                ),
                User(
                    id=hrbp_id,
                    tenant_id=tenant_id,
                    name="HRBP",
                    email=f"{hrbp_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                ),
                AsyncTask(
                    id=task_id,
                    tenant_id=tenant_id,
                    type="interview_digest",
                    status="completed",
                    progress=100,
                    created_by=None,
                ),
                WeeklyReport(
                    id=report_id,
                    tenant_id=tenant_id,
                    period="2026-W35",
                    summary="历史周报",
                    progress_json="[]",
                    risks_json="[]",
                    plan_json="[]",
                    data_sources_json="[]",
                    created_by=None,
                ),
            ]
        )
        await db.commit()
    return admin_id, hrbp_id, task_id, report_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.execute(delete(AsyncTask).where(AsyncTask.tenant_id == tenant_id))
        await db.execute(delete(WeeklyReport).where(WeeklyReport.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


def test_admin_inventory_reports_only_current_tenant_ownerless_work(client: TestClient) -> None:
    tenant_id, other_tenant_id = str(uuid4()), str(uuid4())
    admin_id, _, task_id, report_id = asyncio.run(_seed(tenant_id))
    asyncio.run(_seed(other_tenant_id))
    try:
        response = client.get(
            "/api/admin/users/legacy-work",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": [
                {"work_id": task_id, "work_type": "async_task", "title": "面谈纪要分析"},
                {"work_id": report_id, "work_type": "weekly_report", "title": "周报 2026-W35"},
            ],
            "total": 2,
        }
    finally:
        asyncio.run(_cleanup(tenant_id))
        asyncio.run(_cleanup(other_tenant_id))


def test_admin_claims_one_legacy_task_for_an_hrbp_and_audits_it(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id, hrbp_id, task_id, _ = asyncio.run(_seed(tenant_id))
    try:
        response = client.put(
            f"/api/admin/users/legacy-work/async_task/{task_id}/owner",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
            json={"user_id": hrbp_id},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "work_id": task_id,
            "work_type": "async_task",
            "owner_user_id": hrbp_id,
        }

        async def load() -> tuple[str | None, list[str]]:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                owner = await db.scalar(
                    select(AsyncTask.created_by).where(
                        AsyncTask.tenant_id == tenant_id,
                        AsyncTask.id == task_id,
                    )
                )
                actions = list(
                    (
                        await db.execute(
                            select(AuditLog.scenario_id).where(
                                AuditLog.tenant_id == tenant_id,
                                AuditLog.user_id == admin_id,
                            )
                        )
                    ).scalars()
                )
            return owner, actions

        owner, actions = asyncio.run(load())
        assert owner == hrbp_id
        assert actions == ["legacy_work.claimed"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_claims_one_legacy_weekly_report_for_an_hrbp(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id, hrbp_id, _, report_id = asyncio.run(_seed(tenant_id))
    try:
        response = client.put(
            f"/api/admin/users/legacy-work/weekly_report/{report_id}/owner",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
            json={"user_id": hrbp_id},
        )

        assert response.status_code == 200, response.text

        async def load_owner() -> str | None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return await db.scalar(
                    select(WeeklyReport.created_by).where(
                        WeeklyReport.tenant_id == tenant_id,
                        WeeklyReport.id == report_id,
                    )
                )

        assert asyncio.run(load_owner()) == hrbp_id
    finally:
        asyncio.run(_cleanup(tenant_id))


async def _seed_unscoped_candidates_and_ownerless_content(tenant_id: str) -> tuple[str, str, str]:
    """Seed one unscoped knowledge candidate and one ownerless culture draft."""
    admin_id, candidate_id, content_id = str(uuid4()), str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=admin_id,
                tenant_id=tenant_id,
                name="Admin",
                email=f"{admin_id}@example.test",
                hashed_password="x",
                role="admin",
            )
        )
        await db.flush()
        db.add_all(
            [
                KnowledgeFeedbackCandidate(
                    id=candidate_id,
                    tenant_id=tenant_id,
                    org_unit_id=None,
                    source_user_id=None,
                    source_type="no_evidence",
                    question="历史遗留的无归属问题",
                    question_key="",
                    occurrences=1,
                ),
                CultureContent(
                    id=content_id,
                    tenant_id=tenant_id,
                    created_by=None,
                    keywords_json="[]",
                    news_article="历史无归属文化草稿",
                    group_notice="notice",
                    employee_story="story",
                    event_copy="event",
                    tone="formal",
                ),
            ]
        )
        await db.commit()
    return admin_id, candidate_id, content_id


async def _cleanup_scoped(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id))
        await db.execute(delete(CultureContent).where(CultureContent.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


def test_admin_inventory_also_reports_unscoped_candidates_and_ownerless_drafts(client: TestClient) -> None:
    """Quarantined rows must be countable, never silently invisible."""
    tenant_id = str(uuid4())
    admin_id, candidate_id, content_id = asyncio.run(_seed_unscoped_candidates_and_ownerless_content(tenant_id))
    try:
        response = client.get(
            "/api/admin/users/legacy-work",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        entries = {(item["work_id"], item["work_type"]) for item in payload["items"]}
        assert (candidate_id, "knowledge_feedback_candidate") in entries
        assert (content_id, "culture_content") in entries
    finally:
        asyncio.run(_cleanup_scoped(tenant_id))


def test_admin_claims_unscoped_candidate_by_assigning_source_user(client: TestClient) -> None:
    """An unscoped candidate becomes owned via source_user_id, not created_by."""
    tenant_id = str(uuid4())
    admin_id, candidate_id, _ = asyncio.run(_seed_unscoped_candidates_and_ownerless_content(tenant_id))
    hrbp_id = str(uuid4())
    try:
        async def ensure_hrbp() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                db.add(
                    User(
                        id=hrbp_id,
                        tenant_id=tenant_id,
                        name="HRBP",
                        email=f"{hrbp_id}@example.test",
                        hashed_password="x",
                        role="hrbp",
                    )
                )
                await db.commit()

        asyncio.run(ensure_hrbp())

        response = client.put(
            f"/api/admin/users/legacy-work/knowledge_feedback_candidate/{candidate_id}/owner",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
            json={"user_id": hrbp_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["owner_user_id"] == hrbp_id

        async def load_source_user() -> str | None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return await db.scalar(
                    select(KnowledgeFeedbackCandidate.source_user_id).where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.id == candidate_id,
                    )
                )

        assert asyncio.run(load_source_user()) == hrbp_id
    finally:
        asyncio.run(_cleanup_scoped(tenant_id))


def test_admin_claims_ownerless_culture_draft(client: TestClient) -> None:
    """An ownerless culture draft is claimed by setting created_by."""
    tenant_id = str(uuid4())
    admin_id, _, content_id = asyncio.run(_seed_unscoped_candidates_and_ownerless_content(tenant_id))
    hrbp_id = str(uuid4())
    try:
        async def ensure_hrbp() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                db.add(
                    User(
                        id=hrbp_id,
                        tenant_id=tenant_id,
                        name="HRBP",
                        email=f"{hrbp_id}@example.test",
                        hashed_password="x",
                        role="hrbp",
                    )
                )
                await db.commit()

        asyncio.run(ensure_hrbp())

        response = client.put(
            f"/api/admin/users/legacy-work/culture_content/{content_id}/owner",
            headers={"Authorization": f"Bearer {_token(tenant_id, admin_id)}"},
            json={"user_id": hrbp_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["owner_user_id"] == hrbp_id

        async def load_created_by() -> str | None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return await db.scalar(
                    select(CultureContent.created_by).where(
                        CultureContent.tenant_id == tenant_id,
                        CultureContent.id == content_id,
                    )
                )

        assert asyncio.run(load_created_by()) == hrbp_id
    finally:
        asyncio.run(_cleanup_scoped(tenant_id))
