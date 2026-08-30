"""Scenario task endpoints must enforce creator visibility, not just tenant RLS."""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from starlette.requests import Request

from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask
from app.data.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="module")


def _request(tenant_id: str, user_id: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    request.state.user_role = "hrbp"
    return request


async def test_interview_task_detail_and_history_are_owner_scoped():
    from app.access.routes.interview_digest import get_history, get_progress
    from app.shared.errors import NotFoundError

    tenant_id, actor_id, other_id = str(uuid4()), str(uuid4()), str(uuid4())
    actor_task_id, other_task_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(id=actor_id, tenant_id=tenant_id, name="A", email=f"{actor_id}@example.com", hashed_password="x", role="hrbp"),
                User(id=other_id, tenant_id=tenant_id, name="B", email=f"{other_id}@example.com", hashed_password="x", role="hrbp"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                AsyncTask(id=actor_task_id, tenant_id=tenant_id, type="interview_digest", created_by=actor_id, status="completed", result_json="{}"),
                AsyncTask(id=other_task_id, tenant_id=tenant_id, type="interview_digest", created_by=other_id, status="failed", error_message="hidden"),
            ]
        )
        await db.commit()

    try:
        with pytest.raises(NotFoundError):
            await get_progress(other_task_id, _request(tenant_id, actor_id))

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            history = await get_history(_request(tenant_id, actor_id), session=db)
        assert [item["task_id"] for item in history["digests"]] == [actor_task_id]
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AsyncTask).where(AsyncTask.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()
