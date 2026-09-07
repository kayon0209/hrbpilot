"""Minimal, tenant-scoped in-app notification delivery records."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class InAppNotification(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """One idempotent recipient delivery; body data stays in its source aggregate."""

    __tablename__ = "in_app_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "recipient_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_in_app_notifications_tenant_recipient",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["hr_cases.tenant_id", "hr_cases.id"],
            name="fk_in_app_notifications_tenant_case",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tool_execution_id"],
            ["tool_executions.tenant_id", "tool_executions.id"],
            name="fk_in_app_notifications_tenant_execution",
        ),
        UniqueConstraint("tenant_id", "delivery_key", name="uq_in_app_notifications_tenant_delivery"),
        Index("ix_in_app_notifications_recipient_unread", "tenant_id", "recipient_user_id", "read_at"),
    )

    recipient_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    case_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template: Mapped[str] = mapped_column(String(80), nullable=False)
    delivery_key: Mapped[str] = mapped_column(String(160), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
