from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from app.access.policies.contracts import ToolCatalog, ToolDefinition, ToolKind
from app.access.policies.hr_case import evaluate_hr_case_tool
from app.application.commands import CommandExecutor
from app.runtime.contracts import (
    ConversationTurn,
    ExecutionProfile,
    OutcomeKind,
    RunEnvelope,
    RunLimits,
    VersionedEvent,
)
from app.runtime.events import EventReader
from app.runtime.state import (
    ApprovalState,
    BusinessStatus,
    CaseLifecycle,
    ToolExecutionState,
    project_business_status,
    project_case,
)
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, TOOL_KINDS


class Payload(BaseModel):
    value: str


def test_run_envelope_rejects_mixing_controlled_outcome_and_failure() -> None:
    with pytest.raises(ValidationError, match="either an outcome or a failure"):
        RunEnvelope(
            run_id="r",
            tenant_id="t",
            scenario_id="policy_qa",
            profile=ExecutionProfile.CONVERSATION_TURN,
            outcome_kind=OutcomeKind.COMPLETED,
            failure_code="internal_bug",
        )


def test_profile_and_versioned_event_are_explicit() -> None:
    turn = ConversationTurn(run_id="r", tenant_id="t", scenario_id="policy_qa", session_id="s", message_id="m")
    event = VersionedEvent(
        event_type="conversation.persisted",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        tenant_id="t",
        run_id="r",
        payload=Payload(value="visible answer"),
    )
    assert turn.profile is ExecutionProfile.CONVERSATION_TURN
    assert event.payload.value == "visible answer"


def test_write_tool_requires_effectively_once_or_reconciliation_contract() -> None:
    with pytest.raises(ValidationError, match="idempotency or reconciliation"):
        ToolDefinition(
            name="write",
            version="v1",
            kind=ToolKind.WRITE,
            required_capability="case.write",
            risk_level="high",
            timeout_seconds=10,
            max_attempts=1,
        )

    assert ToolDefinition(
        name="write",
        version="v1",
        kind=ToolKind.WRITE,
        required_capability="case.write",
        risk_level="high",
        timeout_seconds=10,
        max_attempts=1,
        supports_idempotency=True,
    ).supports_idempotency


def test_projection_precedence_is_server_owned() -> None:
    assert project_business_status(CaseLifecycle.OPEN, None, ApprovalState.PENDING, None) == "待审批"
    assert project_business_status(CaseLifecycle.OPEN, None, None, ToolExecutionState.UNKNOWN) == "待确认"
    projection = project_case("case-1", CaseLifecycle.OPEN, None, ApprovalState.PENDING, None)
    assert projection.business_status is BusinessStatus.AWAITING_APPROVAL


def test_tool_catalog_resolves_exact_version_and_rejects_duplicates() -> None:
    read_v1 = ToolDefinition(
        name="knowledge.read",
        version="v1",
        kind=ToolKind.READ,
        required_capability="knowledge.read",
        risk_level="low",
        timeout_seconds=10,
        max_attempts=1,
    )
    catalog = ToolCatalog(version="catalog-1", tools=(read_v1,))
    assert catalog.resolve("knowledge.read", "v1") == read_v1
    with pytest.raises(ValueError, match="absent from catalog"):
        catalog.resolve("knowledge.read", "v2")
    with pytest.raises(ValidationError, match="duplicate name/version"):
        ToolCatalog(version="catalog-1", tools=(read_v1, read_v1))


def test_legacy_hr_case_tool_kind_view_is_derived_from_the_unique_catalog() -> None:
    assert {tool.name: tool.kind.value for tool in TOOL_CATALOG.tools} == TOOL_KINDS
    assert TOOL_CATALOG.resolve("assign_case_owner", "v1").approval_required is True


def test_hr_case_policy_returns_explicit_fail_closed_decision() -> None:
    allowed = evaluate_hr_case_tool(
        TOOL_CATALOG,
        tool_name="assign_case_owner",
        tool_version="v1",
        subject_id="hrbp-1",
        subject_role="hrbp",
        object_ref="hr_case:case-1",
    )
    denied = evaluate_hr_case_tool(
        TOOL_CATALOG,
        tool_name="assign_case_owner",
        tool_version="v1",
        subject_id="employee-1",
        subject_role="employee",
        object_ref="hr_case:case-1",
    )
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason_code == "missing_required_capability"


class PayloadV2(BaseModel):
    value: str
    version: int


def test_versioned_event_requires_registered_reader_and_upcaster() -> None:
    reader = EventReader()
    reader.register("case.changed", 2, PayloadV2)
    reader.register_upcaster("case.changed", 1, lambda payload: PayloadV2(value=payload.value, version=2))
    event = VersionedEvent(
        event_type="case.changed",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        tenant_id="t",
        payload=Payload(value="old"),
    )
    assert reader.read(event, target_version=2).model_dump() == {"value": "old", "version": 2}


def test_run_limits_reject_expired_deadline() -> None:
    with pytest.raises(ValidationError, match="deadline must be in the future"):
        RunLimits(max_tokens=1, deadline_at=datetime.now(UTC) - timedelta(seconds=1))


class FakeUow:
    committed = 0
    rolled_back = 0

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


@pytest.mark.asyncio
async def test_command_executor_owns_commit_and_rollback() -> None:
    executor, successful = CommandExecutor(), FakeUow()

    async def succeed(_command: str, _uow: FakeUow) -> str:
        return "ok"

    assert await executor.execute("command", succeed, successful) == "ok"
    assert (successful.committed, successful.rolled_back) == (1, 0)

    failed = FakeUow()

    async def fail(_command: str, _uow: FakeUow) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await executor.execute("command", fail, failed)
    assert (failed.committed, failed.rolled_back) == (0, 1)
