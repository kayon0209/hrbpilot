"""Agent 任务投影 —— 外部 AI Agent 发起的「意图 → 任务」的持久化记录。

为什么是「投影」而不是第四张业务表
----------------------------------
审批（``approval_requests``）、工作任务（``work_tasks``）、异步执行
（``async_tasks``）各自有明确语义，方案 §1.2 明确禁止把它们合并成一张大表。
本表记录的是**网关层**的事实：外部用户说了什么、服务端冻结了哪版草稿、
用户确认的是哪个 hash、任务现在处于统一状态机的哪一格，以及它指向哪些
内部对象（approval_id / work_task_id / case_id / async_task_id 只是引用）。

绑定 client_id / installation_id 的用途与审批表相同：撤销一次授权后，
躺在草稿或队列里的任务必须能被同一规则挡住，而不是只挡住新调用。

goal_summary 是脱敏摘要，不是完整对话
--------------------------------------
默认不保存外部对话原文（方案 §8.2 的数据流向承诺）。这里只存服务端
规范化后的最小必要参数（canonical_params_json）与一句给人看的业务摘要。
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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

#: 统一状态机的全部取值。迁移与测试都以这份清单为准。
AGENT_TASK_STATUSES = (
    "draft",
    "input_required",
    "ready_for_confirmation",
    "awaiting_approval",
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
)

_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "expired")


class AgentTask(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """一个外部 Agent 任务（草稿 → 确认 → 审批 → 执行）的服务端投影。"""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_tasks_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "requester_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_agent_tasks_tenant_requester",
        ),
        CheckConstraint(
            "status IN ('draft', 'input_required', 'ready_for_confirmation', "
            "'awaiting_approval', 'queued', 'running', 'succeeded', 'failed', "
            "'cancelled', 'expired')",
            name="ck_agent_tasks_status",
        ),
        CheckConstraint("draft_version >= 1", name="ck_agent_tasks_draft_version"),
        # 提交幂等：同一发起人 + 同一 idempotency key 只会有一行完成过提交。
        # 部分唯一索引（NULL 不约束）与 work_tasks 的既有做法一致。
        Index(
            "uq_agent_tasks_submit_key",
            "tenant_id",
            "requester_user_id",
            "submit_idempotency_key",
            unique=True,
            postgresql_where=text("submit_idempotency_key IS NOT NULL"),
        ),
        Index("ix_agent_tasks_requester_status", "tenant_id", "requester_user_id", "status"),
    )

    requester_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: 发起任务的客户端与安装实例。平台自签（网页工作台）为 None。
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    installation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_surface: Mapped[str] = mapped_column(
        String(20), nullable=False, default="other"
    )  # codex|workbuddy|web|other

    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    goal_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    interpreted_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    canonical_params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: sha256(task_type + 规范化 canonical_params)。确认与提交都对齐它 ——
    #: 用户确认的必须是服务端冻结的草稿，而不是客户端临时拼出的参数。
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")

    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    risk_reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    planned_steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    #: 对现有内部对象的引用投影（不复制它们的状态语义）。
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    work_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    async_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    submit_idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<AgentTask id={self.id} type={self.task_type} status={self.status} v{self.draft_version}>"


class AgentTaskEvent(Base, UUIDPrimaryKey, TenantMixin):
    """Agent 任务的事件流水（append-only）。

    只存安全摘要与状态变化：不存 token、不存完整提示词、不存员工敏感原文。
    seq 与 case_events 同模式 —— 任务内单调，由服务层在行锁下分配。
    """

    __tablename__ = "agent_task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_agent_task_events_task_seq"),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["agent_tasks.tenant_id", "agent_tasks.id"],
            name="fk_agent_task_events_tenant_task",
        ),
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: 安全摘要（哪个字段补了、审批结果、执行到第几步）。禁止放参数原文。
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<AgentTaskEvent task={self.task_id} seq={self.seq} type={self.event_type}>"
