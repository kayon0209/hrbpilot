"""Orthogonal lifecycles and server-owned user projections."""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.runtime.contracts import ObservedRunState


class CaseLifecycle(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CONSUMED = "consumed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ToolExecutionState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED = "reconciled_failed"


class BusinessStatus(StrEnum):
    INTAKE = "待受理"
    IN_PROGRESS = "处理中"
    AWAITING_APPROVAL = "待审批"
    AWAITING_CONFIRMATION = "待确认"
    HANDED_OFF = "已转人工"
    COMPLETED = "已完成"


class CaseProjection(BaseModel):
    """Server-owned business view; clients do not derive this state locally."""

    case_id: str = Field(min_length=1)
    lifecycle: CaseLifecycle
    run_state: ObservedRunState | None = None
    approval_state: ApprovalState | None = None
    tool_state: ToolExecutionState | None = None
    business_status: BusinessStatus


def project_business_status(
    lifecycle: CaseLifecycle,
    run_state: ObservedRunState | None,
    approval_state: ApprovalState | None,
    tool_state: ToolExecutionState | None,
) -> BusinessStatus:
    if lifecycle is CaseLifecycle.CLOSED:
        return BusinessStatus.COMPLETED
    if approval_state is ApprovalState.PENDING:
        return BusinessStatus.AWAITING_APPROVAL
    if run_state is ObservedRunState.HANDED_OFF:
        return BusinessStatus.HANDED_OFF
    if tool_state is ToolExecutionState.UNKNOWN:
        return BusinessStatus.AWAITING_CONFIRMATION
    if run_state in {ObservedRunState.QUEUED, ObservedRunState.RUNNING}:
        return BusinessStatus.IN_PROGRESS
    return BusinessStatus.INTAKE


def project_case(
    case_id: str,
    lifecycle: CaseLifecycle,
    run_state: ObservedRunState | None,
    approval_state: ApprovalState | None,
    tool_state: ToolExecutionState | None,
) -> CaseProjection:
    return CaseProjection(
        case_id=case_id,
        lifecycle=lifecycle,
        run_state=run_state,
        approval_state=approval_state,
        tool_state=tool_state,
        business_status=project_business_status(lifecycle, run_state, approval_state, tool_state),
    )
