"""Connector backbone models — sync cursors, idempotent event log and the
one-time OAuth authorization nonce (CSRF state).  All reference data_sources
through composite (tenant_id, source_id) FKs (020).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class ConnectorSyncCursor(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Incremental resume point per (source, stream)."""

    __tablename__ = "connector_sync_cursors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "stream", name="uq_connector_cursor_scope"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_sync_cursors_tenant_source",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(100), nullable=False)
    cursor: Mapped[str] = mapped_column(Text, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectorEventLog(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Every consumed external event — UNIQUE(tenant, source, external id)
    makes consumption idempotent; replay_count tracks redeliveries."""

    __tablename__ = "connector_event_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'failed')",
            name="ck_connector_event_log_status",
        ),
        UniqueConstraint("tenant_id", "source_id", "external_event_id", name="uq_connector_event_consumed"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_event_log_tenant_source",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The durable event inbox is distinct from its downstream business effect.
    # ``processed_at`` is set only by an explicit successful completion.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ConnectorDeliveryAttempt(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """A durable, local-only protocol simulator outbox record.

    This is deliberately separate from the inbound connector event log.  A
    successful status means only that the local simulator accepted the request;
    it never proves external WeCom delivery.
    """

    __tablename__ = "connector_delivery_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'simulated_accepted', 'retryable_failed', 'rejected')",
            name="ck_connector_delivery_attempt_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_connector_delivery_attempt_count"),
        UniqueConstraint(
            "tenant_id",
            "employee_request_id",
            "content_digest",
            name="uq_connector_delivery_attempt_business_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "employee_request_id"],
            ["employee_requests.tenant_id", "employee_requests.id"],
            name="fk_connector_delivery_attempt_tenant_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_delivery_attempt_tenant_source",
        ),
    )

    employee_request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="wecom_simulator")
    recipient_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    message_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_msgid: Mapped[str | None] = mapped_column(String(255), default=None)
    provider_errcode: Mapped[int | None] = mapped_column(Integer, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ConnectorIdentityBinding(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Administrator-verified platform identity to internal employee mapping.

    Provider IDs are never matched heuristically to names or e-mail addresses.
    A connector event must use this tenant- and source-scoped binding before it
    can create an employee-visible HR request.
    """

    __tablename__ = "connector_identity_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "external_user_id", name="uq_connector_identity_binding"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_identity_binding_tenant_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_connector_identity_binding_tenant_user",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)


class ConnectorIntakeEvent(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Restricted reconciliation record for an HR message with no identity binding."""

    __tablename__ = "connector_intake_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_identity', 'materialized')",
            name="ck_connector_intake_event_status",
        ),
        UniqueConstraint("tenant_id", "source_id", "external_event_id", name="uq_connector_intake_event"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "external_event_id"],
            [
                "connector_event_log.tenant_id",
                "connector_event_log.source_id",
                "connector_event_log.external_event_id",
            ],
            name="fk_connector_intake_event_source_event",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_identity")
    employee_request_id: Mapped[str | None] = mapped_column(String(36), default=None)


class OAuthNonce(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """One-time, expiring CSRF nonce for an in-flight OAuth authorization.

    NOT the business lifecycle field ``data_sources.oauth_state`` — this row
    is the anti-CSRF ticket: unpredictable, bound to the tenant/source/actor
    that started the flow, expiring after OAUTH_NONCE_TTL_MINUTES, and
    consumed exactly once (either by a successful callback or by the first
    final failure) so a stolen ``state`` cannot be replayed.
    """

    __tablename__ = "oauth_nonces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "nonce_sha256", name="uq_oauth_nonce_scope"),
        Index("ix_oauth_nonces_expires_at", "expires_at"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_oauth_nonces_tenant_source",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # SHA-256 hex of the plaintext nonce — the nonce itself is only ever
    # shown in the provider redirect URL and must never be logged.
    nonce_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
