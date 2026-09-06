"""Real PostgreSQL checks for outbox lease fencing.

The test uses a random tenant and rolls every write back.  It is intentionally
opt-in with the same flag as the other concurrency checks.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.data.database import make_tenant_session
from app.data.models.runtime import OutboxMessage
from app.outbox.repository import OutboxRepository


@pytest.mark.asyncio
async def test_expired_outbox_lease_can_be_reclaimed_but_stale_worker_cannot_complete() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL lease-fencing verification")

    tenant_id = str(uuid4())
    now = datetime.now(UTC)
    producer = await make_tenant_session(tenant_id)
    worker_one = await make_tenant_session(tenant_id)
    worker_two = await make_tenant_session(tenant_id)
    try:
        message = OutboxMessage(
            tenant_id=tenant_id,
            event_type="tool.dispatch",
            schema_version=1,
            queue_name="tool-write",
            aggregate_type="tool_execution",
            aggregate_id=str(uuid4()),
            dedupe_key=str(uuid4()),
            payload_digest="0" * 64,
            next_attempt_at=now,
        )
        producer.add(message)
        await producer.commit()

        first = await OutboxRepository(worker_one, tenant_id).claim_next("worker-1", 1, now)
        assert first is not None
        first_token = first.lease_token
        await worker_one.commit()

        second = await OutboxRepository(worker_two, tenant_id).claim_next("worker-2", 1, now + timedelta(seconds=2))
        assert second is not None
        assert second.id == message.id
        assert second.lease_token == first_token + 1
        await worker_two.commit()

        stale_completed = await OutboxRepository(worker_one, tenant_id).mark_delivered(
            message.id, "worker-1", first_token
        )
        assert stale_completed is False
        await worker_one.rollback()

        await worker_two.execute(delete(OutboxMessage).where(OutboxMessage.id == message.id))
        await worker_two.commit()
    finally:
        await producer.rollback()
        await worker_one.rollback()
        await worker_two.rollback()
        await producer.close()
        await worker_one.close()
        await worker_two.close()
