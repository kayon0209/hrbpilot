"""HR Case Agent — tool whitelist with typed I/O schemas (Phase 5).

Every tool has: pydantic input schema, an explicit kind (read/write),
and structured error codes. Write tools ALWAYS require a human approval
request before ``begin_tool_execution`` will record them.

The agent never sees raw DB/HTTP clients — it only names a tool and
supplies params; the service layer re-validates everything.
"""

from pydantic import BaseModel, Field


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
    channel: str = Field("email", pattern="^(email|in_app)$")
    recipient_ref: str = Field(..., min_length=1, max_length=120)
    template: str = Field(..., min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)


class UpdateCaseStatusInput(BaseModel):
    status: str = Field(..., min_length=1, max_length=30)


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "search_policy": SearchPolicyInput,
    "get_policy_source": GetPolicySourceInput,
    "create_hr_case": CreateHRCaseInput,
    "assign_case_owner": AssignCaseOwnerInput,
    "send_case_notification": SendCaseNotificationInput,
    "update_case_status": UpdateCaseStatusInput,
}

TOOL_KINDS: dict[str, str] = {
    "search_policy": "read",
    "get_policy_source": "read",
    "create_hr_case": "write",
    "assign_case_owner": "write",
    "send_case_notification": "write",
    "update_case_status": "write",
}


def validate_tool_call(tool_name: str, params: dict) -> dict:
    """Validate a tool call against its schema; returns normalized params.

    Raises ToolError with code UNKNOWN_TOOL / INVALID_PARAMS on failure.
    """
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise ToolError("UNKNOWN_TOOL", f"Tool {tool_name} is not whitelisted")
    try:
        return schema.model_validate(params).model_dump(exclude_none=True)
    except Exception as e:
        raise ToolError("INVALID_PARAMS", str(e)) from e
