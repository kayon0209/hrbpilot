"""HR Case Agent — tool whitelist with typed I/O schemas (Phase 5).

Every tool has: pydantic input schema, an explicit kind (read/write),
and structured error codes. Write tools ALWAYS require a human approval
request before ``begin_tool_execution`` will record them.

The agent never sees raw DB/HTTP clients — it only names a tool and
supplies params; the service layer re-validates everything.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.access.policies.contracts import ToolCatalog, ToolDefinition, ToolKind
from app.access.scopes import Scope


class ToolError(Exception):
    """Structured tool failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class SearchPolicyInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    kb_id: str | None = None
    top_k: int = Field(3, ge=1, le=10)


class GetPolicySourceInput(BaseModel):
    document_name: str = Field(..., min_length=1, max_length=200)
    section: str | None = None


class GetMyAccessProfileInput(BaseModel):
    """无参数：要读的是"我自己"，任何入参都只会变成可伪造的输入。"""


class SearchCasesInput(BaseModel):
    limit: int = Field(20, ge=1, le=50)
    status: str | None = Field(None, max_length=30)
    category: str | None = Field(None, max_length=50)


class CaseIdInput(BaseModel):
    """按案件读取的入参。``case_id`` 必填 —— 不存在"不指定就返回全部"的用法。"""

    case_id: str = Field(..., min_length=1, max_length=36)


class ApprovalStatusInput(BaseModel):
    case_id: str = Field(..., min_length=1, max_length=36)
    #: 可选：只查某一条。不传则返回该案件下的审批列表。
    #: **不能**只凭 approval_id 查 —— 那样 id 就变成了猜测即可命中的凭据。
    approval_id: str | None = Field(None, max_length=36)


class CreateHRCaseInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    subject_ref: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., min_length=1, max_length=50)
    risk_level: str = Field("LOW", pattern="^(LOW|MEDIUM|HIGH)$")
    description: str | None = Field(None, max_length=4000)


class AssignCaseOwnerInput(BaseModel):
    owner_id: str = Field(..., min_length=1, max_length=36)


class SendCaseNotificationInput(BaseModel):
    channel: Literal["in_app"] = "in_app"
    recipient_ref: str = Field(..., min_length=1, max_length=36)
    template: str = Field(..., min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)


class UpdateCaseStatusInput(BaseModel):
    status: str = Field(..., pattern="^RESOLVED$")


class CreateWorkTaskInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    next_action: str = Field("", max_length=1000)
    owner_user_id: str | None = Field(None, min_length=1, max_length=36)
    waiting_for: str | None = Field(None, max_length=200)
    due_at: datetime | None = None
    total_units: int | None = Field(None, ge=1, le=10000)


class ToolOutput(BaseModel):
    summary: str = ""


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "search_policy": SearchPolicyInput,
    "get_policy_source": GetPolicySourceInput,
    "get_my_access_profile": GetMyAccessProfileInput,
    "search_cases": SearchCasesInput,
    "get_case_summary": CaseIdInput,
    "get_approval_status": ApprovalStatusInput,
    "create_hr_case": CreateHRCaseInput,
    "assign_case_owner": AssignCaseOwnerInput,
    "send_case_notification": SendCaseNotificationInput,
    "update_case_status": UpdateCaseStatusInput,
    "create_work_task": CreateWorkTaskInput,
}

_TOOL_METADATA = {
    # 读工具的能力名必须取自 RBAC 矩阵里真实存在的取值。这里原先是
    # "policy.read"——一个 ROLE_CAPABILITIES 里从来没有过的名字，意味着
    # app/access/policies/hr_case.py 的 `required_capability in capabilities`
    # 判定对读工具永远是 False（只是读工具当前不经过那条策略路径，
    # 所以没有暴露成线上故障）。制度检索对应的既有能力是 "policy_qa"。
    #
    # (kind, capability, scope, risk_level)
    #
    # 每个工具都必须声明 scope：它是外部 Agent 授权时的对外契约。判定见
    # app/mcp/auth/authorization.py。
    "search_policy": (ToolKind.READ, "policy_qa", Scope.POLICY_READ, "low"),
    "get_policy_source": (ToolKind.READ, "policy_qa", Scope.POLICY_READ, "low"),
    # 读取"这份凭据能做什么"。所有角色都应该能问这个问题 —— 否则用户无法自查
    # "为什么某个工具在我这里不见了"，只能去猜。
    "get_my_access_profile": (ToolKind.READ, "self_profile", Scope.PROFILE_READ, "low"),
    # 案件上下文闭环（WP7）。三个都只读，都按 ACL 收窄 —— 能看见才读得到。
    "search_cases": (ToolKind.READ, "hr_case", Scope.CASE_READ, "low"),
    "get_case_summary": (ToolKind.READ, "hr_case", Scope.CASE_READ, "low"),
    "get_approval_status": (ToolKind.READ, "hr_case", Scope.APPROVAL_READ, "low"),
    "create_hr_case": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "medium"),
    "assign_case_owner": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "medium"),
    "send_case_notification": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "medium"),
    "update_case_status": (ToolKind.WRITE, "hr_case", Scope.CASE_PROPOSE, "medium"),
    # create_work_task 的能力是 work_summary（它写的是工作台任务），但它的 scope
    # 是 CASE_PROPOSE —— 因为它和其余写工具一样，是**针对某个案件**提交一条待审批
    # 的办理请求，而不是直接落库。scope 表达的是"这类动作需要什么授权"，
    # 而不是"这条记录属于哪个模块"，两者在这里本来就不同。刻意不为它新增一个
    # `hrb:task:propose`：scope 词表是对外契约，多一个取值就要多一轮客户端授权。
    "create_work_task": (ToolKind.WRITE, "work_summary", Scope.CASE_PROPOSE, "medium"),
}

TOOL_CATALOG = ToolCatalog(
    version="hr-case-v1",
    tools=tuple(
        ToolDefinition(
            name=name,
            version="v1",
            kind=kind,
            input_schema=schema.model_json_schema(),
            output_schema=ToolOutput.model_json_schema(),
            required_capability=capability,
            required_scope=required_scope,
            risk_level=risk_level,
            approval_required=kind is ToolKind.WRITE,
            timeout_seconds=30,
            max_attempts=3 if kind is ToolKind.WRITE else 1,
            supports_idempotency=kind is ToolKind.WRITE,
        )
        for name, schema in TOOL_SCHEMAS.items()
        for kind, capability, required_scope, risk_level in (_TOOL_METADATA[name],)
    ),
)

# Compatibility view for the legacy agent loop.  It is derived from the one
# catalog above, so planners, approvals, and execution do not define tool kind
# independently.
TOOL_KINDS: dict[str, str] = {tool.name: tool.kind.value for tool in TOOL_CATALOG.tools}


def validate_tool_call(tool_name: str, params: dict) -> dict:
    """Validate a tool call against its schema; returns normalized params.

    Raises ToolError with code UNKNOWN_TOOL / INVALID_PARAMS on failure.
    """
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise ToolError("UNKNOWN_TOOL", f"Tool {tool_name} is not whitelisted")
    try:
        return schema.model_validate(params).model_dump(mode="json", exclude_none=True)
    except Exception as e:
        raise ToolError("INVALID_PARAMS", str(e)) from e
