"""Persistent, tenant-scoped coordination records for governed runtime work.

These records deliberately store references and digests, not raw prompts,
employee materials, or model output.  Business payloads remain in their
bounded aggregate or protected object store.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class ExecutionGrant(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Short-lived, precise authorization to execute one governed action."""

    __tablename__ = "execution_grants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'consumed', 'revoked', 'expired')",
            name="ck_execution_grants_status",
        ),
        CheckConstraint("max_consumptions >= 1", name="ck_execution_grants_max_consumptions"),
        CheckConstraint(
            "consumption_count >= 0 AND consumption_count <= max_consumptions",
            name="ck_execution_grants_consumption_count",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_execution_grants_tenant_id"),
        Index("ix_execution_grants_claimable", "tenant_id", "status", "expires_at"),
    )

    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[str] = mapped_column(String(120), nullable=False)
    allowed_tool: Mapped[str] = mapped_column(String(120), nullable=False)
    allowed_action: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_params_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    permissions_version: Mapped[str] = mapped_column(String(120), nullable=False)
    issued_for_run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_consumptions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consumption_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class OutboxMessage(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Transactional dispatch request with a lease; no raw business payload."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'leased', 'delivered', 'terminal', 'dead_letter')",
            name="ck_outbox_messages_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_messages_attempt_count"),
        UniqueConstraint("tenant_id", "id", name="uq_outbox_messages_tenant_id"),
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_messages_tenant_dedupe"),
        Index("ix_outbox_messages_claimable", "status", "next_attempt_at", "lease_expires_at"),
    )

    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_name: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(120), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_ref: Mapped[str | None] = mapped_column(Text, default=None)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120), default=None)
    lease_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error_code: Mapped[str | None] = mapped_column(String(80), default=None)
