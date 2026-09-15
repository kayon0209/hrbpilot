"""任务型 MCP 工具目录 —— 普通 HR 的 7+2 个高频工具（方案 §6.4）。

与原子目录（``app/scenarios/hr_case_agent/tools.py``）的关系
----------------------------------------------------------
同一套 ``ToolDefinition`` 契约、同一个 ``authorize_tool_call`` 判定；这里只是
**另一份 bundle**：任务型 facade 挂在 ``/mcp/tasks``，原子工具保留在 ``/mcp``
作为高级/兼容端点。两份目录按名字互不重叠，合并视图（``MCP_DISCOVERY_CATALOG``）
供发现接口与传输守卫使用 —— 守卫对两条出口共用同一个纯函数判定，不存在
"第二套规则"。

task_type 是稳定对外契约
-----------------------
``prepare_hr_action`` 的返回里暴露的是任务类型（case_followup 等），不是底层
审批工具名。底层怎么换，外部客户端看到的类型不变 —— 这正是文档 §6.2
「稳定任务类型，不直接暴露底层函数名」的落点。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.access.policies.contracts import ToolCatalog, ToolDefinition, ToolKind
from app.access.scopes import Scope


class TaskToolOutput(BaseModel):
    summary: str = ""


class PrepareHrActionInput(BaseModel):
    model_config = {"extra": "forbid"}

    goal: str = Field(..., min_length=1, max_length=1000)
    case_ref: str | None = Field(None, max_length=64)
    response_format: Literal["concise", "detailed"] = "concise"


class SubmitHrActionInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=36)
    draft_version: int = Field(..., ge=1)
    idempotency_key: str = Field(..., min_length=8, max_length=64)

    model_config = {"extra": "forbid"}


class ProvideTaskInputInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=36)
    #: 只补缺失字段；不接受整包参数替换 —— 冻结草稿的修改必须逐字段、留事件。
    fields: dict[str, str] = Field(default_factory=dict, max_length=8)


class TaskIdInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=36)


class CancelTaskInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=36)


class ListMyTasksInput(BaseModel):
    status: str | None = Field(None, max_length=30)
    limit: int = Field(20, ge=1, le=20)
    offset: int = Field(0, ge=0, le=980)
    response_format: Literal["concise", "detailed"] = "concise"


class GetTaskStatusInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=36)
    response_format: Literal["concise", "detailed"] = "concise"


class AnswerPolicyQuestionInput(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(3, ge=1, le=10)
    response_format: Literal["concise", "detailed"] = "concise"


class GetCaseContextInput(BaseModel):
    case_ref: str = Field(..., min_length=1, max_length=64)
    response_format: Literal["concise", "detailed"] = "concise"


TASK_TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "prepare_hr_action": PrepareHrActionInput,
    "submit_hr_action": SubmitHrActionInput,
    "provide_task_input": ProvideTaskInputInput,
    "get_task_status": GetTaskStatusInput,
    "list_my_tasks": ListMyTasksInput,
    "cancel_task": CancelTaskInput,
    "answer_policy_question": AnswerPolicyQuestionInput,
    "get_case_context": GetCaseContextInput,
}

#: (kind, capability, scope, risk) —— 与原子目录同一语义：scope 是"这类动作需要
#: 什么授权"，判定仍由三重交集（角色能力 ∩ scope ∩ 客户端上限）完成。
#: 读类任务工具（状态/列表）用 CASE_READ：查询套餐即可完成"找到任务并继续处理"；
#: 草稿生命周期工具用 CASE_PROPOSE：它们是写路径的入口，只读凭据不应产生草稿。
_TASK_TOOL_METADATA: dict[str, tuple[ToolKind, str, Scope, str]] = {
    "prepare_hr_action": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "low"),
    "submit_hr_action": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "medium"),
    "provide_task_input": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "low"),
    "cancel_task": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "low"),
    "get_task_status": (ToolKind.READ, "hr_case", Scope.CASE_READ, "low"),
    "list_my_tasks": (ToolKind.READ, "hr_case", Scope.CASE_READ, "low"),
    "answer_policy_question": (ToolKind.READ, "policy_qa", Scope.POLICY_READ, "low"),
    "get_case_context": (ToolKind.READ, "hr_case", Scope.CASE_READ, "low"),
}

#: 这些工具会在服务端创建草稿/审批记录，kind=WRITE 让 approval_required/idempotency
#: 等约束沿用同一套构造规则；``approval_required`` 的语义是"最终副作用要人工审批"，
#: prepare/provide/cancel 本身不产生业务写入，标记集中在 submit 与 catalog 消费方。
AGENT_TASK_CATALOG = ToolCatalog(
    version="agent-task-v1",
    tools=tuple(
        ToolDefinition(
            name=name,
            version="v1",
            kind=kind,
            input_schema=schema.model_json_schema(),
            output_schema=TaskToolOutput.model_json_schema(),
            required_capability=capability,
            required_scope=required_scope,
            risk_level=risk_level,
            approval_required=kind is ToolKind.WRITE and name == "submit_hr_action",
            timeout_seconds=30,
            max_attempts=3 if kind is ToolKind.WRITE else 1,
            supports_idempotency=kind is ToolKind.WRITE,
        )
        for name, schema in TASK_TOOL_SCHEMAS.items()
        for kind, capability, required_scope, risk_level in (_TASK_TOOL_METADATA[name],)
    ),
)

TASK_TOOL_NAMES = tuple(t.name for t in AGENT_TASK_CATALOG.tools)


def validate_task_tool_call(tool_name: str, params: dict) -> dict:
    """任务型工具的入参校验（与原子工具 ``validate_tool_call`` 同形的错误码）。"""
    from app.scenarios.hr_case_agent.tools import ToolError

    schema = TASK_TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise ToolError("UNKNOWN_TOOL", f"Task tool {tool_name} is not whitelisted")
    try:
        return schema.model_validate(params).model_dump(mode="json", exclude_none=True)
    except Exception as e:
        raise ToolError("INVALID_PARAMS", str(e)) from e


#: 两条出口合并后的发现目录：传输守卫与 access profile 用它回答"这份凭据
#: 到底能调哪些工具"，保证 /mcp 与 /mcp/tasks 的可见性与判定共用同一份事实。
def combined_catalog() -> ToolCatalog:
    from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

    return ToolCatalog(version="mcp-discovery-v1", tools=tuple(TOOL_CATALOG.tools) + tuple(AGENT_TASK_CATALOG.tools))
