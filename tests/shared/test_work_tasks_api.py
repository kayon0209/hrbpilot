"""Persistent multi-day work-task API journeys."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.user import User
from app.data.models.work_task import WorkTask
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


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


async def _seed_users(tenant_id: str) -> tuple[str, str]:
    user_id, other_user_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    name="Task owner",
                    email=f"{user_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                ),
                User(
                    id=other_user_id,
                    tenant_id=tenant_id,
                    name="Other HRBP",
                    email=f"{other_user_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                ),
            ]
        )
        await db.commit()
    return user_id, other_user_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(WorkTask).where(WorkTask.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


def test_hrbp_can_create_a_persistent_multi_day_task(client: TestClient) -> None:
    tenant_id = str(uuid4())
    user_id, _other_user_id = asyncio.run(_seed_users(tenant_id))
    try:
        response = client.post(
            "/api/work-summaries/tasks",
            headers={"Authorization": f"Bearer {_token(tenant_id, user_id)}"},
            json={
                "title": "完成三地薪酬复核",
                "next_action": "先核对华东数据",
                "due_at": "2026-09-04T10:00:00Z",
                "total_units": 3,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["title"] == "完成三地薪酬复核"
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_task_split_completion_summary_and_object_acl(client: TestClient) -> None:
    tenant_id = str(uuid4())
    user_id, other_user_id = asyncio.run(_seed_users(tenant_id))
    owner_headers = {"Authorization": f"Bearer {_token(tenant_id, user_id)}"}
    other_headers = {"Authorization": f"Bearer {_token(tenant_id, other_user_id)}"}
    try:
        created = client.post(
            "/api/work-summaries/tasks",
            headers=owner_headers,
            json={"title": "三地薪酬复核", "next_action": "拆分地区任务"},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        subtask = client.post(
            f"/api/work-summaries/tasks/{task_id}/subtasks",
            headers=owner_headers,
            json={"title": "完成华东复核", "next_action": "核对差异并留痕"},
        )
        assert subtask.status_code == 201, subtask.text
        subtask_id = subtask.json()["task_id"]

        completed = client.patch(
            f"/api/work-summaries/tasks/{subtask_id}",
            headers=owner_headers,
            json={"status": "completed"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completed_at"] is not None

        forbidden = client.patch(
            f"/api/work-summaries/tasks/{task_id}",
            headers=other_headers,
            json={"status": "completed"},
        )
        assert forbidden.status_code == 404, forbidden.text

        summary = client.get("/api/work-summaries", headers=owner_headers)
        assert summary.status_code == 200, summary.text
        payload = summary.json()
        all_items = [
            *(payload["attention"] or []),
            *(payload["completed_today"] or []),
            *([payload["continue_work"]] if payload["continue_work"] else []),
        ]
        parent = next(item for item in all_items if item["work_id"] == task_id)
        assert parent["progress_mode"] == "units"
        assert parent["completed_units"] == 1
        assert parent["total_units"] == 1
        assert any(item["work_id"] == subtask_id for item in payload["completed_today"])
    finally:
        asyncio.run(_cleanup(tenant_id))
