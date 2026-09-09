"""Material library models — the employee dimension that was missing.

Employees are the anchor entity for work materials: interview records and
voice entries both reference an employee (nullable for anonymous surveys).
Raw corpus text is persisted alongside the async analysis task so results
are traceable, re-analysable and searchable by employee.

All three tables follow the house rules: TenantMixin + composite tenant FKs
+ FORCE RLS (enabled from day one, lesson from migration 020/032).
"""

from datetime import date, datetime

from sqlalchemy import ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class Employee(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Employee dimension — get-or-create by (tenant_id, name) on ingest."""

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_employees_tenant_id"),
        UniqueConstraint("tenant_id", "employee_no", name="uq_employees_tenant_no"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_employees_tenant_creator",
        ),
    )

    employee_no: Mapped[str | None] = mapped_column(String(50), default=None)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), default=None)
    # active | offboarded — display filter, not auth
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<Employee id={self.id} name={self.name}>"


class InterviewRecord(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """One interview (面谈纪要) — raw corpus + link to its analysis task."""

    __tablename__ = "interview_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_interview_records_tenant_id"),
        UniqueConstraint("tenant_id", "digest_task_id", name="uq_interview_records_tenant_task"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_interview_records_tenant_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            ["employees.tenant_id", "employees.id"],
            name="fk_interview_records_tenant_employee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "digest_task_id"],
            ["async_tasks.tenant_id", "async_tasks.id"],
            name="fk_interview_records_tenant_task",
        ),
    )

    employee_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    # Free-text denormalized snapshot — list rows must not require a join
    employee_name: Mapped[str | None] = mapped_column(String(100), default=None)
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    # general | performance | exit | onboarding | communication（自由扩展）
    interview_type: Mapped[str] = mapped_column(String(30), nullable=False, default="general")
    interview_date: Mapped[date | None] = mapped_column(default=None)
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    raw_text_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    digest_task_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    # analyzing | completed | failed — mirrors the linked task's stage
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="analyzing")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<InterviewRecord id={self.id} employee={self.employee_name} status={self.status}>"


class VoiceEntry(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """One employee-voice entry — anonymous surveys leave employee unset."""

    __tablename__ = "voice_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_voice_entries_tenant_id"),
        UniqueConstraint("tenant_id", "digest_task_id", name="uq_voice_entries_tenant_task"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_voice_entries_tenant_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            ["employees.tenant_id", "employees.id"],
            name="fk_voice_entries_tenant_employee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "digest_task_id"],
            ["async_tasks.tenant_id", "async_tasks.id"],
            name="fk_voice_entries_tenant_task",
        ),
    )

    employee_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    employee_name: Mapped[str | None] = mapped_column(String(100), default=None)
    # survey | inbox | townhall | interview（自由扩展）
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="survey")
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    raw_text_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    digest_task_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="analyzing")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<VoiceEntry id={self.id} channel={self.channel} status={self.status}>"
