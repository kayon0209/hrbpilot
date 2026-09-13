"""Single vocabulary for planner, approval, and execution policy checks."""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.access.scopes import Scope


class ToolKind(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    kind: ToolKind
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    required_capability: str = Field(min_length=1)
    #: 该工具所需的 OAuth scope（取值见 ``app.access.scopes.Scope``）。
    #:
    #: 刻意**必填且是枚举类型**，而不是带默认值的可空字符串：
    #: - 必填 → 新增工具时必须显式决定它需要哪个 scope，没有默认值可以蒙混过去；
    #: - 枚举 → 写错一个 scope 名在**导入期**就报错，而不是等某次调用时静默判 False。
    #: 允许留空等于允许"新工具不参与 scope 判定"，那是一条静默越权路径。
    required_scope: Scope
    risk_level: str = Field(min_length=1)
    approval_required: bool = False
    timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(ge=1)
    supports_idempotency: bool = False
    supports_reconciliation: bool = False

    @model_validator(mode="after")
    def writes_are_safe_to_retry_or_reconcile(self) -> "ToolDefinition":
        if self.kind is ToolKind.WRITE and not (self.supports_idempotency or self.supports_reconciliation):
            raise ValueError("write tools require idempotency or reconciliation support")
        return self


class ToolCatalog(BaseModel):
    """An immutable-by-version catalog with exact tool-version resolution."""

    version: str = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def tool_versions_are_unique(self) -> "ToolCatalog":
        keys = {(tool.name, tool.version) for tool in self.tools}
        if len(keys) != len(self.tools):
            raise ValueError("tool catalog contains duplicate name/version entries")
        return self

    def resolve(self, name: str, version: str) -> ToolDefinition:
        for tool in self.tools:
            if tool.name == name and tool.version == version:
                return tool
        raise ValueError(f"tool {name}@{version} is absent from catalog {self.version}")


class PolicyDecision(BaseModel):
    allowed: bool
    reason_code: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    evaluated_subject: str = Field(min_length=1)
    evaluated_object: str = Field(min_length=1)
    constraints: dict[str, str] = Field(default_factory=dict)
