"""Weekly-report generation must not fake success when persistence fails.

Persists a user + async task on a live PostgreSQL, hence integration.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask
from app.data.models.user import User

pytestmark = pytest.mark.integration


def _token(tenant_id: str, user_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": user_id,
                "role": "hrbp",
                "tenant_id": tenant_id,
                "email": f"{user_id}@example.test",
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


@pytest.mark.asyncio
async def test_generate_fails_loudly_when_report_cannot_be_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 with an empty report_id is a fake success (audit finding 7)."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.scenarios.weekly_report.orchestrator import WeeklyReportOrchestrator

    tenant_id = str(uuid4())
    user_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="周报用户",
                email=f"{user_id}@example.test",
                hashed_password="x",
                role="hrbp",
            )
        )
        await db.flush()
        db.add(
            AsyncTask(
                tenant_id=tenant_id,
                type="interview_digest",
                status="completed",
                progress=100,
                created_by=user_id,
                result_json=json_result(),
            )
        )
        await db.commit()
    # 取回插入的 task id 作为 source_ids
    from sqlalchemy import select

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        source_ids = list(
            (
                await db.execute(
                    select(AsyncTask.id).where(AsyncTask.tenant_id == tenant_id, AsyncTask.created_by == user_id)
                )
            ).scalars()
        )

    class _StubReport:
        def model_dump(self) -> dict:
            return {"summary": "已生成", "progress": [], "risks": [], "plan": []}

    async def _fake_generate(self, **_kwargs: object) -> _StubReport:
        return _StubReport()

    async def _fail_to_store(self, **_kwargs: object) -> str:
        return ""

    monkeypatch.setattr(WeeklyReportOrchestrator, "generate", _fake_generate)
    monkeypatch.setattr(WeeklyReportOrchestrator, "_store_report", _fail_to_store)

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/weekly-report/generate",
            headers={"Authorization": f"Bearer {_token(tenant_id, user_id)}"},
            json={"period": "2026-W35", "source_ids": source_ids, "draft_mode": True},
        )

    assert response.status_code >= 400, (
        f"generate returned {response.status_code} with report_id={response.json().get('report_id')!r}; "
        "persistence failure must surface as an error, not a fake success"
    )

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(AsyncTask).where(AsyncTask.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


def json_result() -> str:
    """A minimal valid InterviewDigestResponse payload."""
    import json

    return json.dumps(
        {
            "employee_demands": [],
            "risk_level": "LOW",
            "risk_signals": [],
            "action_items": [],
            "suggested_owner": "",
            "summary": "面谈纪要摘要内容",
            "confidence": 0.9,
            "has_evidence": True,
        },
        ensure_ascii=False,
    )
