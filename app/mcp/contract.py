"""MCP 返回契约 —— 让「到底发生了什么」在用户侧可判读。

为什么需要它
------------
读工具的执行层（``app/scenarios/hr_case_agent/read_executors.py``）早就区分了
两种失败：**命中为零**（"检索通道可用但无匹配，应按无依据处理"）与
**基础设施不可用**（``ToolError("RETRIEVAL_UNAVAILABLE")``）。但这层语义在往
外走的时候被压平了 —— MCP 协议出口返回 ``note``，REST 桥返回 ``ok=true`` 加
一句 ``warning``，于是"我没查到"和"我坏了"对调用方（尤其是 AI 助手）长得一样，
两者都会被执行成一句含糊的"已受理"。

这里定义**唯一一套** outcome 值与中文用户文案，MCP 协议出口与 REST 桥共用。
新增工具时只需在此登记它的"证据字段"（见 ``_EVIDENCE_KEYS``），
判定即自动生效 —— 不要在各出口各写一份分支。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ToolOutcome(str, Enum):
    """一次工具调用的终态。

    刻意只保留**代码里真实可达**的终态 —— 不登记那些"听起来合理但没有产生
    路径"的状态（例如：读工具在取证失败时不会退化成"部分成功"，它就是 FAILED，
    因为没有任何依据被返回）。
    """

    FOUND = "FOUND"  # 有依据（命中片段 / 水合到正文）
    NO_EVIDENCE = "NO_EVIDENCE"  # 通道可用，但没有匹配 —— 应按「无依据」处理，不是错误
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # 写工具：已生成待审批记录，尚未执行
    INPUT_REQUIRED = "INPUT_REQUIRED"  # 任务缺少必填信息，等待补充
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # 服务端冻结草稿，等待用户确认
    RUNNING = "RUNNING"  # 已排队或正在执行，真实进度由任务投影返回
    SUCCEEDED = "SUCCEEDED"  # 已得到真实产出
    CANCELLED = "CANCELLED"  # 用户取消且未继续副作用
    EXPIRED = "EXPIRED"  # 草稿/审批超过有效期
    AUTH_REQUIRED = "AUTH_REQUIRED"  # 未携带身份，无法确定作用范围
    FORBIDDEN = "FORBIDDEN"  # 身份有效，但当前角色不持有该能力
    FAILED = "FAILED"  # 调用失败（含依赖不可达）


#: outcome → 面向普通用户的中文说明。不出现技术名词、不出现内部路径/异常字符串。
USER_MESSAGES: dict[ToolOutcome, str] = {
    ToolOutcome.FOUND: "查询完成，已找到相关内容。",
    ToolOutcome.NO_EVIDENCE: "没有找到匹配的内容。这不代表制度里没有规定，建议换一种说法，或交给 HR 人工确认。",
    ToolOutcome.AWAITING_APPROVAL: "已提交，等待 HR 批准后才会真正执行。",
    ToolOutcome.INPUT_REQUIRED: "信息还不完整，请补齐后再生成确认卡。",
    ToolOutcome.AWAITING_CONFIRMATION: "草稿已冻结，请核对确认卡并提交审批。",
    ToolOutcome.RUNNING: "任务正在处理中，可稍后查询状态。",
    ToolOutcome.SUCCEEDED: "任务已完成。",
    ToolOutcome.CANCELLED: "任务已取消。",
    ToolOutcome.EXPIRED: "草稿已过期，请重新发起。",
    ToolOutcome.AUTH_REQUIRED: "这次调用没有携带身份信息，无法确定它在哪个单位、哪个权限范围内执行，因此没有返回任何真实数据。",
    ToolOutcome.FORBIDDEN: "你当前的权限不包含这项能力，请联系管理员。",
    ToolOutcome.FAILED: "调用没有完成，请稍后重试或联系管理员。",
}

#: 已经可以"处理完毕"的终态 —— 用于 ``ok`` 字段的向后兼容。
_SUCCEEDED: frozenset[ToolOutcome] = frozenset(
    {
        ToolOutcome.FOUND,
        ToolOutcome.NO_EVIDENCE,
        ToolOutcome.AWAITING_APPROVAL,
        ToolOutcome.AWAITING_CONFIRMATION,
        ToolOutcome.INPUT_REQUIRED,
        ToolOutcome.RUNNING,
        ToolOutcome.SUCCEEDED,
        ToolOutcome.CANCELLED,
        ToolOutcome.EXPIRED,
    }
)

#: 读工具 → 「有依据」体现在哪个字段上。新增读工具必须在此登记，
#: 否则一律按 NO_EVIDENCE 处理（宁可保守，也不要谎报命中了）。
_EVIDENCE_KEYS: dict[str, str] = {
    "search_policy": "chunks",
    "get_policy_source": "document",
}

#: ToolError.code / AppError.code → 用户文案。未登记的 code 一律回落到通用失败
#: 文案，**不把原始异常字符串透给用户**（原始信息只进日志）。
FAILURE_MESSAGES: dict[str, str] = {
    "NO_KNOWLEDGE_BASE": "当前单位还没有可用的制度库，请先让管理员在「知识库管理」里启用一个。",
    "RETRIEVAL_UNAVAILABLE": "制度检索服务暂时不可用，请稍后重试。本次没有返回任何制度内容，不会用推测补上。",
    "TENANT_CONTEXT_MISSING": USER_MESSAGES[ToolOutcome.AUTH_REQUIRED],
    "UNKNOWN_TOOL": "这个能力不在可用范围内。",
    "INVALID_PARAMS": "参数不完整或不合法，请检查后重试。",
    "CASE_ID_REQUIRED": "办理类操作需要先指定要办理的案件：请先从案件列表里打开对应事项，再提交。",
    # 案件/审批侧（app/scenarios/hr_case_agent/service.py 的 AppError 子类）
    "INVALID_CASE_TRANSITION": "这个案件已经有一条待处理的办理请求，请先在「团队待处理」里处理它，再提交新的。",
    "APPROVAL_INVALID": "这条办理请求已失效（可能已被处理或已过期），请重新提交。",
    "CASE_PERMISSION_DENIED": USER_MESSAGES[ToolOutcome.FORBIDDEN],
    "HIGH_RISK_WRITE_BLOCKED": "这类事项不能由 AI 直接办理，只能先收集材料并转人工处理。",
    "NOT_FOUND": "没有找到对应的案件，请核对案件编号。",
    "TASK_NOT_FOUND": "没有找到这个任务，或它不属于当前用户。",
    "TASK_NOT_EDITABLE": "这个任务已经进入审批或执行阶段，不能再补充信息；如需调整请取消后重新发起。",
    "TASK_NOT_SUBMITTABLE": "草稿信息还不完整或已有提交，当前状态无法提交。请先到任务中心核对状态。",
    "TASK_STALE_VERSION": "你确认的是旧版本的草稿。请重新获取最新草稿并确认。",
    "TASK_HASH_MISMATCH": "冻结草稿校验未通过，不能提交。请重新获取草稿。",
    "TASK_ALREADY_SUBMITTED": "这个动作已经提交过一次，不会重复创建新审批。",
    "TASK_EXPIRED": "草稿已过期。请重新发起一个新任务。",
    "TASK_NOT_CANCELLABLE": "这个任务已经不能取消（已批准/已执行或已终态）。",
    "TASK_OWNER_UNRESOLVED": "负责人或接收人需要是系统内唯一成员：请在任务中心选择已注册的成员后重试。",
    "TASK_TYPE_UNDETECTED": "这句话暂时不支持转成任务：请换一种说法，或在任务中心手动创建。",
}

#: 错误码 → ``fix``（怎么改才能通过）与 ``retryable``（同一输入重试是否有意义）。
#:
#: 目的：让调用方（尤其是外部 AI Agent）**能自纠错**，而不是拿一个裸
#: ``INVALID_PARAMS`` 盲目重试。``fix`` 写的是"哪个字段、约束是什么、正确格式
#: 长什么样"；``retryable=False`` 的错误重试一万次也不会成功，模型应当改参数
#: 或改问法，而不是再调一次。
#:
#: 未登记的 code：``fix=None`` + ``retryable=False`` —— 没有可靠的自纠路径时，
#: 诚实地告诉模型"别重试"，比编一条似是而非的指引更安全。
FAILURE_HINTS: dict[str, tuple[str | None, bool]] = {
    "INVALID_PARAMS": (
        "参数校验失败：请对照错误详情里指出的字段补全或修正取值。"
        "常见约束：query 不能为空且不超过 500 字；case_id 必须是完整案件编号"
        "（先用 search_cases 拿到编号）；top_k 在 1–10 之间。",
        False,
    ),
    "CASE_ID_REQUIRED": ("先调用 search_cases 找到案件编号，再带上 case_id 重新提交。", False),
    "UNKNOWN_TOOL": (None, False),
    "NO_KNOWLEDGE_BASE": ("请管理员在「知识库管理」里为当前单位启用制度库后再查询。", False),
    "RETRIEVAL_UNAVAILABLE": ("依赖的检索服务暂时不可用，可稍后用相同参数重试。", True),
    "INVALID_CASE_TRANSITION": ("请先在「团队待处理」里处理该案件的既有请求，再提交新的。", False),
    "APPROVAL_INVALID": ("这条请求已失效，请用相同参数重新提交一条新的。", False),
    "NOT_FOUND": ("案件编号可能不对：先用 search_cases 列出案件，确认编号后再试。", False),
    "INTERNAL_ERROR": ("服务内部错误，可稍后重试；若持续失败请联系管理员。", True),
    "TASK_NOT_EDITABLE": ("该任务不在草稿阶段，补参无效；如需调整请取消后重新发起。", False),
    "TASK_STALE_VERSION": ("使用get_task_status获取最新草稿版本后重新确认。", False),
    "TASK_OWNER_UNRESOLVED": ("指定唯一的系统成员姓名，或在工作台中选择成员。", False),
    "TASK_EXPIRED": ("草稿已过期，请重新用prepare_hr_action发起新任务。", False),
    "TASK_NOT_CANCELLABLE": ("已进入不可撤销阶段的任务不能取消；先用get_task_status查看真实进度。", False),
}


#: 写工具提交成功后统一的用户说明。刻意包含"不会重复建单"这一事实：
#: `HRCaseService.request_approval` 会复用同参数的待审批记录，所以这里既
#: 描述了新建，也描述了沿用的情形 —— 两种情况下这句话都成立。
APPROVAL_SUBMITTED_TEMPLATE = (
    "已提交（编号 {approval_id}），等待 HR 批准后才会真正执行。"
    "同一案件上参数相同的请求只会保留一条待审批记录，重复提交不会重复建单。"
)


def is_ok(outcome: ToolOutcome) -> bool:
    """该终态是否表示"本次调用被正常受理"。"""
    return outcome in _SUCCEEDED


def read_outcome(tool_name: str, payload: dict[str, Any]) -> ToolOutcome:
    """从读工具的执行结果推导 outcome（唯一判据，两个出口共用）。"""
    key = _EVIDENCE_KEYS.get(tool_name)
    if key is None:
        # 未登记证据字段的工具：有内容即视为已找到，否则按无依据处理。
        return ToolOutcome.FOUND if payload else ToolOutcome.NO_EVIDENCE
    return ToolOutcome.FOUND if payload.get(key) else ToolOutcome.NO_EVIDENCE


def envelope(
    tool_name: str,
    outcome: ToolOutcome,
    *,
    user_message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """构造统一返回信封。

    ``ok`` 与 ``tool`` 保持向后兼容（既有调用方与测试依赖它们）；
    ``outcome`` / ``user_message`` 是新增的判断依据。
    """
    payload: dict[str, Any] = {
        "ok": is_ok(outcome),
        "tool": tool_name,
        "outcome": outcome.value,
        "user_message": user_message or USER_MESSAGES[outcome],
    }
    payload.update(extra)
    return payload


def failure_envelope(
    tool_name: str,
    code: str,
    *,
    detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """失败信封：只暴露受控的 ``error_code``，不透出原始异常内容。

    - ``fix``：怎么改才能通过（哪个字段、什么约束、正确格式）。没有可靠指引
      的错误码给 ``None`` —— 编一条似是而非的指引比没有更糟。
    - ``retryable``：同一输入重试是否有意义。``False`` 时模型应当改参数或改
      问法，而不是原样再调一次。
    - ``detail``：受控的补充信息（例如 pydantic 校验错误里"哪个字段不满足什么
      约束"）。**只放结构化的校验事实，不放原始异常的堆栈或内部路径**。
    """
    fix, retryable = FAILURE_HINTS.get(code, (None, False))
    payload = envelope(
        tool_name,
        ToolOutcome.FAILED,
        user_message=FAILURE_MESSAGES.get(code, USER_MESSAGES[ToolOutcome.FAILED]),
        error_code=code,
        fix=fix,
        retryable=retryable,
        **extra,
    )
    if detail:
        payload["detail"] = detail
    return payload
