"""Database-only transactional outbox operations.

Callers own the surrounding Unit of Work.  In particular, this module never
commits a transaction and never invokes an external consumer while a database
transaction is open.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.runtime import OutboxMessage


class OutboxRepository:
    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def _claimable(
        self,
        now: datetime,
        *,
        event_type: str | None = None,
        aggregate_type: str | None = None,
    ) -> Select[tuple[OutboxMessage]]:
        predicates = [
            OutboxMessage.tenant_id == self._tenant_id,
            OutboxMessage.next_attempt_at <= now,
            or_(
                OutboxMessage.status == "pending",
                and_(
                    OutboxMessage.status == "leased",
                    OutboxMessage.lease_expires_at.is_not(None),
                    OutboxMessage.lease_expires_at <= now,
                ),
            ),
        ]
        if event_type is not None:
            predicates.append(OutboxMessage.event_type == event_type)
        if aggregate_type is not None:
            predicates.append(OutboxMessage.aggregate_type == aggregate_type)
        return (
            select(OutboxMessage)
            .where(*predicates)
            .order_by(OutboxMessage.next_attempt_at, OutboxMessage.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def enqueue(self, message: OutboxMessage) -> OutboxMessage:
        """Stage a message in the caller-owned transaction; never commit here."""
        if message.tenant_id != self._tenant_id:
            raise ValueError("outbox message tenant does not match repository tenant")
        self._session.add(message)
        await self._session.flush()
        return message

    async def claim_next(
        self,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
        *,
        event_type: str | None = None,
        aggregate_type: str | None = None,
    ) -> OutboxMessage | None:
        """Claim one eligible typed message; concurrent workers skip each other's rows."""
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or datetime.now(UTC)
        message = (
            await self._session.execute(
                self._claimable(
                    claimed_at,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                )
            )
        ).scalar_one_or_none()
        if message is None:
            return None
        message.status = "leased"
        message.lease_owner = worker_id
        message.lease_token += 1
        message.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        message.attempt_count += 1
        await self._session.flush()
        return message

    async def mark_delivered(self, message_id: str, worker_id: str, lease_token: int) -> bool:
        """Complete only the active lease; a stale worker cannot overwrite state."""
        completed = await self._session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == message_id,
                OutboxMessage.tenant_id == self._tenant_id,
                OutboxMessage.status == "leased",
                OutboxMessage.lease_owner == worker_id,
                OutboxMessage.lease_token == lease_token,
            )
            .values(status="delivered", lease_owner=None, lease_expires_at=None)
            .returning(OutboxMessage.id)
        )
        return completed.scalar_one_or_none() is not None

    async def schedule_retry(
        self,
        message_id: str,
        worker_id: str,
        lease_token: int,
        next_attempt_at: datetime,
        error_code: str,
    ) -> bool:
        """Release only the active lease for a retryable failure."""
        released = await self._session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == message_id,
                OutboxMessage.tenant_id == self._tenant_id,
                OutboxMessage.status == "leased",
                OutboxMessage.lease_owner == worker_id,
                OutboxMessage.lease_token == lease_token,
            )
            .values(
                status="pending",
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=next_attempt_at,
                last_error_code=error_code,
            )
            .returning(OutboxMessage.id)
        )
        return released.scalar_one_or_none() is not None

    async def mark_terminal(
        self,
        message_id: str,
        worker_id: str,
        lease_token: int,
        error_code: str,
        *,
        dead_letter: bool = False,
    ) -> bool:
        """Stop delivery only for the active lease, optionally in the DLQ."""
        completed = await self._session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == message_id,
                OutboxMessage.tenant_id == self._tenant_id,
                OutboxMessage.status == "leased",
                OutboxMessage.lease_owner == worker_id,
                OutboxMessage.lease_token == lease_token,
            )
            .values(
                status="dead_letter" if dead_letter else "terminal",
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error_code,
            )
            .returning(OutboxMessage.id)
        )
        return completed.scalar_one_or_none() is not None
