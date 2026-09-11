"""Regression tests for the stale-task timeout sweep (audit 2026-08-31 P0-1).

``async_tasks`` is FORCE RLS: a sweep without an explicit tenant context
silently matched zero rows, leaving dead-worker tasks in ``pending`` forever
(13-day-old proof row found in production data). The sweep must take a
tenant, scope the UPDATE to it, and mark only that tenant's stale rows failed.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select


def test_expire_stale_tasks_requires_tenant_argument():
    """The old no-context call form must fail loudly, not expire zero rows silently."""
    import inspect

    from app.scenarios.tasks import expire_stale_tasks

    params = inspect.signature(expire_stale_tasks).parameters
    assert "tenant_id" in params, "sweep must carry the tenant context"
    assert params["tenant_id"].default is inspect.Parameter.empty, "tenant_id must be required"


@pytest.mark.asyncio
async def test_sweep_marks_only_the_calling_tenants_stale_tasks():
    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask
    from app.scenarios.tasks import expire_stale_tasks

    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    task_a, task_b = str(uuid4()), str(uuid4())
    old = datetime.now(UTC) - timedelta(minutes=30)
    fresh = datetime.now(UTC) - timedelta(seconds=5)
    factory = get_session_factory()

    # Each tenant's rows are inserted under that tenant's RLS context.
    for tenant, task_id in ((tenant_a, task_a), (tenant_b, task_b)):
        async with factory() as db:
            db.info["tenant_id"] = tenant
            db.add(
                AsyncTask(
                    id=task_id,
                    tenant_id=tenant,
                    type="document_ingestion",
                    status="pending",
                    created_at=old,
                    updated_at=old,
                )
            )
            await db.commit()

    try:
        expired = await expire_stale_tasks(tenant_a)
        assert expired >= 1, "the tenant's stale row must be updated (old no-context call matched 0 rows)"

        async with factory() as db:
            db.info["tenant_id"] = tenant_a
            row_a = (await db.execute(select(AsyncTask).where(AsyncTask.id == task_a))).scalar_one()
        assert row_a.status == "failed"
        assert "超时" in (row_a.error_message or "")
        assert row_a.completed_at is not None

        async with factory() as db:
            db.info["tenant_id"] = tenant_b
            row_b = (await db.execute(select(AsyncTask).where(AsyncTask.id == task_b))).scalar_one()
        assert row_b.status == "pending", "the sweep must never touch another tenant"

        # A task younger than the timeout window must not be expired.
        young_id = str(uuid4())
        async with factory() as db:
            db.info["tenant_id"] = tenant_a
            db.add(
                AsyncTask(
                    id=young_id,
                    tenant_id=tenant_a,
                    type="interview_digest",
                    status="pending",
                    created_at=fresh,
                    updated_at=fresh,
                )
            )
            await db.commit()
        await expire_stale_tasks(tenant_a)
        async with factory() as db:
            db.info["tenant_id"] = tenant_a
            young = (await db.execute(select(AsyncTask).where(AsyncTask.id == young_id))).scalar_one()
        assert young.status == "pending", "young in-flight tasks must not be expired"
        cleanup_ids = {task_a, young_id}
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_a
            await db.execute(delete(AsyncTask).where(AsyncTask.id.in_(list(cleanup_ids))))
            await db.commit()
        async with factory() as db:
            db.info["tenant_id"] = tenant_b
            await db.execute(delete(AsyncTask).where(AsyncTask.id == task_b))
            await db.commit()


def test_sweep_is_async_and_importable_without_side_effects():
    from app.scenarios.tasks import expire_stale_tasks

    assert asyncio.iscoroutinefunction(expire_stale_tasks)
