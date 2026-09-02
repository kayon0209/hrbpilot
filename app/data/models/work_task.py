"""Persistent user-managed work tasks and subtasks."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class WorkTask(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "work_tasks"
    __table_args__ = (
        # Composite parent FK keeps child rows in the same tenant as their
        # parent at the database level (migration 017).
        UniqueConstraint("tenant_id", "id", name="uq_work_tasks_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_task_id"],
            ["work_tasks.tenant_id", "work_tasks.id"],
            name="fk_work_tasks_tenant_parent",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting', 'completed', 'cancelled')",
            name="ck_work_tasks_status",
        ),
        CheckConstraint(
            "((progress_mode = 'stage' AND total_units IS NULL AND completed_units = 0) "
            "OR (progress_mode = 'units' AND total_units > 0 "
            "AND completed_units >= 0 AND completed_units <= total_units))",
            name="ck_work_tasks_truthful_progress",
        ),
        # FE-04: client-generated idempotency key (partial unique index from
        # migration 022) dedupes retried creation requests.
        Index(
            "uq_work_tasks_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # The tenant-scoped parent binding lives in __table_args__ as a composite
    # FK; the single-column ForeignKey here would defeat it.
    parent_task_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    next_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    waiting_for: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="stage")
    completed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
