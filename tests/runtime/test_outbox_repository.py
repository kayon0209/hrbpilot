from datetime import UTC, datetime

import pytest

from app.outbox.repository import OutboxRepository


class NoQuerySession:
    async def execute(self, _statement: object) -> None:
        raise AssertionError("invalid inputs must fail before querying")


@pytest.mark.asyncio
async def test_outbox_claim_rejects_invalid_lease_inputs_without_querying() -> None:
    repository = OutboxRepository(NoQuerySession(), "tenant-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="worker_id"):
        await repository.claim_next("", 1)
    with pytest.raises(ValueError, match="lease_seconds"):
        await repository.claim_next("worker-1", 0)


def test_outbox_claim_query_requires_pending_or_expired_lease() -> None:
    statement = str(OutboxRepository(NoQuerySession(), "tenant-1")._claimable(datetime.now(UTC)))  # type: ignore[arg-type]
    assert "outbox_messages.status = :status_1" in statement
    assert "outbox_messages.status = :status_2" in statement
    assert "lease_expires_at <=" in statement
    locking = OutboxRepository(NoQuerySession(), "tenant-1")._claimable(datetime.now(UTC))._for_update_arg  # type: ignore[arg-type]
    assert locking is not None
    assert locking.skip_locked is True
