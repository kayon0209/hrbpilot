"""Schema-free contracts for the agent runtime.

These types freeze the cross-scenario vocabulary only.  They intentionally do
not own database rows, queues, approvals, or tool execution.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ExecutionProfile(StrEnum):
    GOVERNED_CASE = "governed_case"
    GENERATION_JOB = "generation_job"
    CONVERSATION_TURN = "conversation_turn"


class DesiredRunState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    HUMAN_TAKEOVER = "human_takeover"


class ObservedRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"
    CANCELLED = "cancelled"


class OutcomeKind(StrEnum):
    COMPLETED = "completed"
    NO_EVIDENCE = "no_evidence"
    POLICY_BLOCKED = "policy_blocked"
    PARTIAL = "partial"
    AWAITING_APPROVAL = "awaiting_approval"
    HANDED_OFF = "handed_off"
    CANCELLED = "cancelled"
    EXTERNAL_OUTCOME_UNKNOWN = "external_outcome_unknown"


class FailureCode(StrEnum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    DEPENDENCY_TIMEOUT = "dependency_timeout"
    RATE_LIMIT = "rate_limit"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    MODEL_BAD_OUTPUT = "model_bad_output"
    CONTEXT_OVERFLOW = "context_overflow"
    TOOL_FAILURE = "tool_failure"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INTERNAL_BUG = "internal_bug"


class RunEnvelope(BaseModel):
    run_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    profile: ExecutionProfile
    initiated_by: str | None = None
    execution_grant_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    desired_state: DesiredRunState = DesiredRunState.RUNNING
    observed_state: ObservedRunState = ObservedRunState.QUEUED
    attempt_no: int = Field(default=1, ge=1)
    fencing_token: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None
    prompt_version: str | None = None
    model_route_version: str | None = None
    retrieval_version: str | None = None
    policy_version: str | None = None
    tool_catalog_version: str | None = None
    event_schema_version: int = Field(default=1, ge=1)
    outcome_kind: OutcomeKind | None = None
    failure_code: FailureCode | None = None
    handoff_reason: str | None = None

    @model_validator(mode="after")
    def outcome_and_failure_are_exclusive(self) -> "RunEnvelope":
        if self.outcome_kind is not None and self.failure_code is not None:
            raise ValueError("a run records either an outcome or a failure, never both")
        return self


class RunLimits(BaseModel):
    max_steps: int | None = Field(default=None, ge=1)
    max_tool_attempts: int = Field(default=0, ge=0)
    max_tokens: int = Field(gt=0)
    deadline_at: datetime

    @model_validator(mode="after")
    def deadline_is_in_the_future(self) -> "RunLimits":
        if self.deadline_at <= datetime.now(self.deadline_at.tzinfo):
            raise ValueError("run deadline must be in the future")
        return self


class GovernedCaseRun(RunEnvelope):
    profile: ExecutionProfile = ExecutionProfile.GOVERNED_CASE
    case_id: str = Field(min_length=1)
    max_steps: int = Field(ge=1)
    max_tool_attempts: int = Field(ge=0)


class GenerationJob(RunEnvelope):
    profile: ExecutionProfile = ExecutionProfile.GENERATION_JOB
    input_ref: str = Field(min_length=1)


class ConversationTurn(RunEnvelope):
    profile: ExecutionProfile = ExecutionProfile.CONVERSATION_TURN
    session_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)


class VersionedEvent(BaseModel):
    event_type: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    occurred_at: datetime
    tenant_id: str = Field(min_length=1)
    run_id: str | None = None
    payload: BaseModel
