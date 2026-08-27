"""HR Case service layer (Phase 4) — the ONLY writer of case state.

Rules enforced here (API layer cannot bypass them):
  - every query and mutation is scoped to the caller's tenant
  - status changes go through the state machine (state.transition)
  - case events are append-only, seq is per-case monotonic
  - approvals must be APPROVED and unexpired before execution; a consumed
    approval cannot be reused (idempotency at the side-effect boundary)
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.hr_case import (
    AgentRun,
    ApprovalRequest,
    CaseEvent,
    CasePlan,
    HRCase,
    ToolExecution,
)
from app.scenarios.hr_case_agent import state as case_state
from app.shared.errors import AppError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)


class CasePermissionDeniedError(AppError):
    """Caller lacks the role required for this case action."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CASE_PERMISSION_DENIED", status_code=403)


class ApprovalError(AppError):
    """Approval is missing, expired, rejected, or already consumed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="APPROVAL_INVALID", status_code=409)


# Roles allowed to decide approvals and execute write tools. The HR Case
# Agent itself never holds these roles — it only proposes.
DECIDER_ROLES = frozenset({"hr_manager", "admin"})

# Phase 5 tool whitelist: write tools ALWAYS require an approval request;
# read tools do not. begin_tool_execution enforces this split.
WRITE_TOOLS = frozenset(
    {"create_hr_case", "assign_case_owner", "send_case_notification", "update_case_status"}
)
READ_TOOLS = frozenset({"search_policy", "get_policy_source"})


class HRCaseService:
    """Tenant-safe service for the HR Case lifecycle."""

    def __init__(self, session: AsyncSession, tenant_id: str, actor: str = "system") -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.actor = actor

    # --- case CRUD ---

    async def create_case(
        self,
        created_by: str,
        subject_ref: str,
        category: str,
        title: str,
        description: str | None = None,
        risk_level: str = "LOW",
    ) -> HRCase:
        case = HRCase(
            tenant_id=self.tenant_id,
            created_by=created_by,
            subject_ref=subject_ref,
            category=category,
            title=title,
            description=description,
            risk_level=risk_level,
            status=case_state.NEW,
        )
        self.session.add(case)
        await self.session.flush()
        await self._append_event(case.id, "CASE_CREATED", {"title": title, "risk_level": risk_level})
        return case

    async def get_case(self, case_id: str) -> HRCase:
        case = (
            await self.session.execute(select(HRCase).where(HRCase.id == case_id, HRCase.tenant_id == self.tenant_id))
        ).scalars().first()
        if case is None:
            raise NotFoundError("HR case", case_id)
        return case

    async def transition_case(self, case_id: str, target: str, reason: str | None = None) -> HRCase:
        case = await self.get_case(case_id)
        previous = case.status
        case.status = case_state.transition(previous, target)
        await self._append_event(
            case.id, "STATUS_CHANGED", {"from": previous, "to": case.status, "reason": reason or ""}
        )
        return case

    # --- events (append-only) ---

    async def _append_event(self, case_id: str, event_type: str, payload: dict, agent_run_id: str | None = None) -> None:
        seq = (
            await self.session.execute(
                select(func.coalesce(func.max(CaseEvent.seq), 0)).where(CaseEvent.case_id == case_id)
            )
        ).scalar_one()
        self.session.add(
            CaseEvent(
                tenant_id=self.tenant_id,
                case_id=case_id,
                agent_run_id=agent_run_id,
                seq=int(seq) + 1,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
                actor=self.actor,
            )
        )
        await self.session.flush()

    async def list_events(self, case_id: str) -> list[CaseEvent]:
        await self.get_case(case_id)  # tenant check
        rows = (
            await self.session.execute(
                select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.seq.asc())
            )
        ).scalars().all()
        return list(rows)

    # --- plans ---

    async def save_plan(
        self,
        case_id: str,
        steps: list[dict],
        rationale: str | None = None,
        risk_notes: str | None = None,
        agent_run_id: str | None = None,
    ) -> CasePlan:
        case = await self.get_case(case_id)
        if case_state.transition(case.status, case_state.PLAN_READY) != case_state.PLAN_READY:
            raise case_state.InvalidTransitionError(case.status, case_state.PLAN_READY)
        plan = CasePlan(
            tenant_id=self.tenant_id,
            case_id=case_id,
            agent_run_id=agent_run_id,
            steps_json=json.dumps(steps, ensure_ascii=False),
            rationale=rationale,
            risk_notes=risk_notes,
        )
        self.session.add(plan)
        await self.session.flush()
        case.status = case_state.PLAN_READY
        await self._append_event(
            case_id, "PLAN_CREATED", {"plan_id": plan.id, "steps": len(steps)}, agent_run_id=agent_run_id
        )
        return plan

    # --- approvals (human-only decisions) ---

    async def request_approval(
        self,
        case_id: str,
        tool_name: str,
        params: dict,
        plan_id: str | None = None,
        agent_run_id: str | None = None,
        ttl_seconds: int = 3600,
    ) -> ApprovalRequest:
        case = await self.get_case(case_id)
        case.status = case_state.transition(case.status, case_state.AWAITING_APPROVAL)
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        # Normalize first so the stored params carry schema defaults; the
        # execution-side re-validation then produces the identical dict.
        from app.scenarios.hr_case_agent.tools import validate_tool_call

        try:
            normalized = validate_tool_call(tool_name, params)
        except Exception as e:
            raise ApprovalError(f"Invalid params for {tool_name}: {e}") from e
        params_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        approval = ApprovalRequest(
            tenant_id=self.tenant_id,
            case_id=case_id,
            plan_id=plan_id,
            tool_name=tool_name,
            params_json=params_json,
            input_hash=_hash_params(tool_name, normalized),
            requested_by=agent_run_id,
            expires_at=expires,
        )
        self.session.add(approval)
        await self.session.flush()
        await self._append_event(
            case_id,
            "APPROVAL_REQUESTED",
            {"approval_id": approval.id, "tool": tool_name},
            agent_run_id=agent_run_id,
        )
        return approval

    async def decide_approval(self, approval_id: str, approver_id: str, decision: str, reason: str | None, role: str) -> ApprovalRequest:
        if role not in DECIDER_ROLES:
            raise CasePermissionDeniedError(f"Role {role} cannot decide approvals")
        approval = (
            await self.session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == self.tenant_id
                )
            )
        ).scalars().first()
        if approval is None:
            raise NotFoundError("Approval request", approval_id)
        if approval.status != "PENDING":
            raise ApprovalError(f"Approval {approval_id} is {approval.status}, not PENDING")
        if approval.expires_at is not None and datetime.now(UTC) > approval.expires_at.replace(
            tzinfo=UTC
        ):
            approval.status = "EXPIRED"
            raise ApprovalError(f"Approval {approval_id} expired")

        if decision == "approve":
            approval.status = "APPROVED"
        elif decision == "reject":
            approval.status = "REJECTED"
        else:
            raise ApprovalError(f"Unknown decision: {decision}")
        approval.approver_id = approver_id
        approval.decision_reason = reason
        approval.decided_at = datetime.now(UTC)
        await self._append_event(
            approval.case_id,
            "APPROVAL_DECIDED",
            {"approval_id": approval.id, "decision": decision, "reason": reason or ""},
        )
        return approval

    # --- tool execution (idempotent, approval-gated) ---

    async def begin_tool_execution(
        self,
        case_id: str,
        tool_name: str,
        params: dict,
        request_id: str,
        approval_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> ToolExecution:
        """Create the execution record for an approved write tool.

        Idempotency: a duplicate (case_id, request_id) returns the existing
        record without re-running the side effect. Approval: write tools
        require an APPROVED, unexpired, unconsumed approval request.
        """
        existing = (
            await self.session.execute(
                select(ToolExecution).where(
                    ToolExecution.case_id == case_id,
                    ToolExecution.request_id == request_id,
                    ToolExecution.tenant_id == self.tenant_id,
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing

        # Approval↔execution binding compares the schema-normalized form
        # (defaults included) so a re-validated call always hashes equal.
        try:
            from app.scenarios.hr_case_agent.tools import validate_tool_call

            normalized_for_hash = validate_tool_call(tool_name, params)
        except Exception:
            normalized_for_hash = params
        input_hash = _hash_params(tool_name, normalized_for_hash)

        if tool_name in WRITE_TOOLS:
            if approval_id is None:
                raise ApprovalError(f"Write tool {tool_name} requires an approval request")
            approval = (
                await self.session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == self.tenant_id
                    )
                )
            ).scalars().first()
            if approval is None:
                raise NotFoundError("Approval request", approval_id)
            if approval.status == "EXPIRED" or (
                approval.expires_at is not None
                and datetime.now(UTC) > approval.expires_at.replace(tzinfo=UTC)
            ):
                raise ApprovalError(f"Approval {approval_id} expired")
            if approval.status != "APPROVED":
                raise ApprovalError(f"Tool {tool_name} requires APPROVED status, got {approval.status}")
            # Byte-for-byte params identity: the approval's stored params and
            # the executed params must hash to the same input_hash.
            if approval.input_hash is not None and approval.input_hash != input_hash:
                raise ApprovalError("Approval does not match the executed params")
            approval.status = "CONSUMED"

        execution = ToolExecution(
            tenant_id=self.tenant_id,
            case_id=case_id,
            approval_id=approval_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            request_id=request_id,
            input_hash=input_hash,
            status="RUNNING",
        )
        self.session.add(execution)
        await self.session.flush()
        await self._append_event(
            case_id,
            "TOOL_EXECUTION_STARTED",
            {"tool": tool_name, "request_id": request_id},
            agent_run_id=agent_run_id,
        )
        return execution

    async def finish_tool_execution(self, execution_id: str, ok: bool, result_summary: str | None = None, error_code: str | None = None, error_message: str | None = None) -> ToolExecution:
        execution = (
            await self.session.execute(
                select(ToolExecution).where(
                    ToolExecution.id == execution_id, ToolExecution.tenant_id == self.tenant_id
                )
            )
        ).scalars().first()
        if execution is None:
            raise NotFoundError("Tool execution", execution_id)
        execution.status = "SUCCEEDED" if ok else "FAILED"
        execution.result_summary = result_summary
        execution.error_code = error_code
        execution.error_message = error_message
        await self._append_event(
            execution.case_id,
            "TOOL_EXECUTION_FINISHED",
            {"tool": execution.tool_name, "ok": ok, "error_code": error_code},
        )
        return execution

    # --- agent runs ---

    async def start_agent_run(self, case_id: str, goal: str) -> AgentRun:
        await self.get_case(case_id)
        run = AgentRun(tenant_id=self.tenant_id, case_id=case_id, goal=goal, status="RUNNING")
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_agent_run(self, run_id: str, status: str, steps_taken: int, tokens_used: int, handoff_reason: str | None = None) -> AgentRun:
        run = (
            await self.session.execute(
                select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == self.tenant_id)
            )
        ).scalars().first()
        if run is None:
            raise NotFoundError("Agent run", run_id)
        run.status = status
        run.steps_taken = steps_taken
        run.tokens_used = tokens_used
        run.handoff_reason = handoff_reason
        return run


def _hash_params(tool_name: str, params: dict) -> str:
    import hashlib

    blob = json.dumps({"tool": tool_name, "params": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
