"""Prepare an approved HR Case write for asynchronous governed dispatch.

This command performs no external call.  Its caller commits once; a dispatcher
later claims the Outbox row, consumes the precise grant, and invokes a tool
outside the database transaction.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.access.policies.hr_case import evaluate_hr_case_tool
from app.approvals.grants import ExecutionGrantRepository, ExecutionGrantSpec
from app.data.models.hr_case import ApprovalRequest, ToolExecution
from app.data.models.runtime import OutboxMessage
from app.outbox.repository import OutboxRepository
from app.scenarios.hr_case_agent.service import ApprovalError, CasePermissionDeniedError, HRCaseService
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call


@dataclass(frozen=True)
class PreparedToolDispatch:
    execution_id: str
    grant_id: str
    outbox_id: str
    deduplicated: bool = False


class ToolGateway:
    """Gateway adapter for the legacy HR Case approval record.

    The current user is the explicit grant subject.  The worker is not allowed
    to infer a system principal from the presence of an approval.
    """

    def __init__(self, service: HRCaseService) -> None:
        self._service = service

    async def prepare_approved_write(self, case_id: str, approval_id: str, request_id: str) -> PreparedToolDispatch:
        if self._service.actor_id is None or self._service.actor_role is None:
            raise CasePermissionDeniedError("governed tool dispatch requires an authenticated user subject")
        approval = await self._load_approval(case_id, approval_id)
        now = datetime.now(UTC)
        normalized = validate_tool_call(approval.tool_name, json.loads(approval.params_json))
        tool = TOOL_CATALOG.resolve(approval.tool_name, "v1")
        decision = evaluate_hr_case_tool(
            TOOL_CATALOG,
            tool_name=tool.name,
            tool_version=tool.version,
            subject_id=self._service.actor_id,
            subject_role=self._service.actor_role,
            object_ref=f"hr_case:{case_id}",
        )
        if not decision.allowed:
            raise CasePermissionDeniedError(decision.reason_code)
        await self._service.get_case(case_id)  # object ACL is evaluated at execution preparation time.
        existing = await self._service.session.scalar(
            select(ToolExecution).where(
                ToolExecution.tenant_id == self._service.tenant_id,
                ToolExecution.case_id == case_id,
                ToolExecution.request_id == request_id,
            )
        )
        if existing is not None:
            if existing.approval_id != approval_id:
                raise ApprovalError(f"Execution {request_id} belongs to a different approval")
            if existing.execution_grant_id is None or existing.outbox_message_id is None:
                raise ApprovalError(f"Execution {request_id} is not managed by the governed dispatcher")
            return PreparedToolDispatch(
                execution_id=existing.id,
                grant_id=existing.execution_grant_id,
                outbox_id=existing.outbox_message_id,
                deduplicated=True,
            )
        if approval.status != "APPROVED":
            raise ApprovalError(f"Approval {approval_id} is {approval.status}, not APPROVED")
        if approval.expires_at is not None and approval.expires_at.replace(tzinfo=UTC) <= now:
            raise ApprovalError(f"Approval {approval_id} expired")
        grant = await ExecutionGrantRepository(self._service.session, self._service.tenant_id).issue(
            ExecutionGrantSpec(
                subject_type="user",
                subject_id=self._service.actor_id,
                capability=tool.required_capability,
                object_type="hr_case",
                object_id=case_id,
                allowed_tool=tool.name,
                allowed_action="execute",
                normalized_params_hash=approval.input_hash,
                policy_version=decision.policy_version,
                permissions_version="rbac-v1",
                expires_at=approval.expires_at or now + timedelta(minutes=5),
            ),
            now,
        )
        execution = await self._service.begin_tool_execution(
            case_id,
            tool.name,
            normalized,
            request_id=request_id,
            approval_id=approval_id,
        )
        execution.status = "PENDING"
        execution.attempt = 0
        outbox = await OutboxRepository(self._service.session, self._service.tenant_id).enqueue(
            OutboxMessage(
                tenant_id=self._service.tenant_id,
                event_type="tool.dispatch",
                schema_version=1,
                queue_name="tool-write",
                aggregate_type="tool_execution",
                aggregate_id=execution.id,
                run_id=execution.agent_run_id,
                dedupe_key=f"tool-dispatch:{execution.id}",
                payload_ref=f"execution_grant:{grant.id}",
                payload_digest=approval.input_hash or "0" * 64,
                next_attempt_at=now,
            )
        )
        execution.execution_grant_id = grant.id
        execution.outbox_message_id = outbox.id
        await self._service.session.flush()
        return PreparedToolDispatch(execution_id=execution.id, grant_id=grant.id, outbox_id=outbox.id)

    async def _load_approval(self, case_id: str, approval_id: str) -> ApprovalRequest:
        approval = await self._service.session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.case_id == case_id,
                ApprovalRequest.tenant_id == self._service.tenant_id,
            )
        )
        if approval is None:
            raise ApprovalError(f"Approval {approval_id} was not found for case {case_id}")
        return approval
