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
    "create_hr_case": CreateHRCaseInput,
    "assign_case_owner": AssignCaseOwnerInput,
    "send_case_notification": SendCaseNotificationInput,
    "update_case_status": UpdateCaseStatusInput,
    "create_work_task": CreateWorkTaskInput,
}

_TOOL_METADATA = {
    "search_policy": (ToolKind.READ, "policy.read", "low"),
    "get_policy_source": (ToolKind.READ, "policy.read", "low"),
    "create_hr_case": (ToolKind.WRITE, "hr_case", "medium"),
    "assign_case_owner": (ToolKind.WRITE, "hr_case", "medium"),
    "send_case_notification": (ToolKind.WRITE, "hr_case", "medium"),
    "update_case_status": (ToolKind.WRITE, "hr_case", "medium"),
    "create_work_task": (ToolKind.WRITE, "work_summary", "medium"),
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
            risk_level=risk_level,
            approval_required=kind is ToolKind.WRITE,
            timeout_seconds=30,
            max_attempts=3 if kind is ToolKind.WRITE else 1,
            supports_idempotency=kind is ToolKind.WRITE,
        )
        for name, schema in TOOL_SCHEMAS.items()
        for kind, capability, risk_level in (_TOOL_METADATA[name],)
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
