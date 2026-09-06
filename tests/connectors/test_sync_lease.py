"""CONN-03: PostgreSQL advisory-lock lease prevents concurrent syncs per source."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.connectors import runner
from app.data.database import get_session_factory
from app.data.models.data_source import DataSource
from app.shared.errors import AppError

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")


async def _seed_source(tenant_id: str) -> str:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = DataSource(
            id=str(uuid4()),
            tenant_id=tenant_id,
            name="并发同步源",
            platform="wecom",
            purpose="lease",
            authorized_scope="x",
            authorized_scope_json={"chat_ids": ["lease-chat"]},
            content_types='["messages"]',
            data_destination="x",
            created_by="lease-test",
            oauth_state="connected",
            oauth_app_id="ww10086",
            credential_encrypted=b"lease-secret",
            oauth_encrypted_token=b"x",
            sync_status="never_run",
        )
        db.add(row)
        await db.commit()
        return row.id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
        await db.commit()


@pytest.mark.asyncio
async def test_second_concurrent_sync_for_same_source_is_rejected() -> None:
    _require()
    tenant_id = str(uuid4())
    source_id = await _seed_source(tenant_id)
    original_run_wecom_messages = runner._run_wecom_messages
    try:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_sync(*args, **kwargs):
            entered.set()
            await release.wait()
            return runner.WECOM_MESSAGE_STREAM

        # First sync grabs the lease and stalls inside the (stubbed) pull.
        orig_load = runner._load_source

        async def fake_load(t, s):
            return await orig_load(t, s)

        runner._load_source = fake_load  # type: ignore[assignment]
        runner._run_wecom_messages = slow_sync  # type: ignore[assignment]

        async def sync_a() -> str:
            return await runner.run_connector_sync(tenant_id, source_id)

        async def sync_b() -> str:
            return await runner.run_connector_sync(tenant_id, source_id)

        task_a = asyncio.create_task(sync_a())
        await asyncio.wait_for(entered.wait(), timeout=10)

        # The second sync must be rejected with 409 CONFLICT, not queued.
        with pytest.raises(AppError) as exc_info:
            await asyncio.wait_for(sync_b(), timeout=10)
        assert exc_info.value.status_code == 409

        release.set()
        await asyncio.wait_for(task_a, timeout=10)
    finally:
        runner._run_wecom_messages = original_run_wecom_messages
        await _cleanup(tenant_id)
