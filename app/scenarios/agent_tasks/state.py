"""Agent 任务状态机 —— 网关层的唯一转移判据。

与案件状态机（``app/scenarios/hr_case_agent/state.py``）同一条规则：
**API / 工具层永不直接写 status**，一切转移经过 ``transition`` 校验。

这里的每个取值都是对 HR 可讲的语义（待补充 / 待确认 / 待审批 / 排队 /
执行中 / 成功 / 失败 / 已取消 / 已过期），而不是内部表的状态副本 ——
审批、工作任务、异步任务各自的内部状态由投影函数映射过来，映射不到就保持
保守的「进行中」，绝不猜一个终态。
"""

from __future__ import annotations

from app.shared.errors import AppError

DRAFT = "draft"
INPUT_REQUIRED = "input_required"
READY_FOR_CONFIRMATION = "ready_for_confirmation"
AWAITING_APPROVAL = "awaiting_approval"
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
EXPIRED = "expired"

ALL_STATUSES: frozenset[str] = frozenset(
    {
        DRAFT,
        INPUT_REQUIRED,
        READY_FOR_CONFIRMATION,
        AWAITING_APPROVAL,
        QUEUED,
        RUNNING,
        SUCCEEDED,
        FAILED,
        CANCELLED,
        EXPIRED,
    }
)

#: 终态不可再迁移（expired 可由 awaiting_approval 进入，见下）。
TERMINAL_STATUSES: frozenset[str] = frozenset({SUCCEEDED, FAILED, CANCELLED, EXPIRED})

#: 草稿期状态：还能改参数、能取消，还没有产生任何业务副作用。
DRAFTISH_STATUSES: frozenset[str] = frozenset({DRAFT, INPUT_REQUIRED, READY_FOR_CONFIRMATION})

TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({INPUT_REQUIRED, READY_FOR_CONFIRMATION, CANCELLED, EXPIRED}),
    INPUT_REQUIRED: frozenset({INPUT_REQUIRED, READY_FOR_CONFIRMATION, CANCELLED, EXPIRED}),
    # 自环（input_required → input_required）表达"又补了一轮，还是缺"：
    # 每轮补参都创建新 draft_version，状态不变但版本变，事件照记。
    READY_FOR_CONFIRMATION: frozenset({AWAITING_APPROVAL, READY_FOR_CONFIRMATION, CANCELLED, EXPIRED}),
    AWAITING_APPROVAL: frozenset({QUEUED, RUNNING, FAILED, EXPIRED}),
    QUEUED: frozenset({RUNNING, SUCCEEDED, FAILED}),
    RUNNING: frozenset({SUCCEEDED, FAILED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
    EXPIRED: frozenset(),
}


class InvalidAgentTaskTransitionError(AppError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot transition agent task from {current} to {target}",
            code="INVALID_TASK_TRANSITION",
            status_code=409,
        )
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def transition(current: str, target: str) -> str:
    """校验并返回目标状态；不允许的转移抛错，而不是静默忽略。"""
    if not can_transition(current, target):
        raise InvalidAgentTaskTransitionError(current, target)
    return target


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES
