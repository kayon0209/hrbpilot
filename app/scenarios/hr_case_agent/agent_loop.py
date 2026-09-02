"""HR Case Agent — bounded execution loop (Phase 5).

Runs a validated plan against a case with hard limits:
  - max steps per run (fuse) → STOPPED_LIMIT + handoff
  - one retry per failing tool, then handoff
  - write tools: plan step → ApprovalRequest (PENDING) → run ends waiting
    for a human; execution of the approved write happens via
    ``execute_approved_write`` in a SEPARATE request (approve ≠ execute)

The loop cannot escalate its own permissions: every tool call passes
``validate_tool_call`` and the service's approval gate again.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.scenarios.hr_case_agent import state as case_state
from app.scenarios.hr_case_agent.planner import MAX_STEPS_PER_RUN, CasePlanDraft, PlanStep
from app.scenarios.hr_case_agent.service import HRCaseService
from app.scenarios.hr_case_agent.tools import TOOL_KINDS, ToolError, validate_tool_call
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

# tool_name -> async executor (params) -> dict result summary.
# Registered by the app at startup; the agent loop only sees this mapping.
ToolExecutor = Callable[[dict], Awaitable[dict]]
TOOL_EXECUTORS: dict[str, ToolExecutor] = {}


def register_tool_executor(tool_name: str, executor: ToolExecutor) -> None:
    if tool_name not in TOOL_KINDS:
        raise ValueError(f"Cannot register non-whitelisted tool {tool_name}")
    TOOL_EXECUTORS[tool_name] = executor


@dataclass
class RunResult:
    status: str  # COMPLETED | AWAITING_APPROVAL | STOPPED_LIMIT | HANDED_OFF | FAILED
    steps_taken: int = 0
    tokens_used: int = 0
    handoff_reason: str | None = None
    approval_id: str | None = None
    tool_results: list[dict] = field(default_factory=list)


async def run_plan(
    service: HRCaseService,
    case_id: str,
    plan: CasePlanDraft,
    agent_run_id: str | None = None,
    start_status: str = "TRIAGED",
) -> RunResult:
    """Execute a validated plan step by step under the bounded-agent policy."""
    result = RunResult(status="RUNNING")
    case = await service.get_case(case_id)
    if case.status == "NEW":
        await service.transition_case(case_id, "TRIAGED")
    if case.status in ("TRIAGED", "NEEDS_CLARIFICATION"):
        await service.transition_case(case_id, "EVIDENCE_READY")

    for index, step in enumerate(plan.steps):
        if result.steps_taken >= MAX_STEPS_PER_RUN:
            result.status = "STOPPED_LIMIT"
            result.handoff_reason = f"step budget {MAX_STEPS_PER_RUN} exhausted at step {index}"
            break

        try:
            normalized = validate_tool_call(step.tool, step.params)
        except ToolError as e:
            result.status = "HANDED_OFF"
            result.handoff_reason = f"step {index}: {e.code}"
            break

        if TOOL_KINDS[step.tool] == "write":
            # Reads gather evidence first; the plan is then frozen and the
            # case enters approval. Execution is a separate request.
            case_now = await service.get_case(case_id)
            if case_now.status == "EVIDENCE_READY":
                await service.transition_case(case_id, case_state.PLAN_READY, reason=f"plan ready for {step.tool}")
            approval = await service.request_approval(
                case_id,
                tool_name=step.tool,
                params=normalized,
                agent_run_id=agent_run_id,
            )
            result.status = "AWAITING_APPROVAL"
            result.approval_id = approval.id
            result.steps_taken += 1
            result.tool_results.append({"tool": step.tool, "approval_id": approval.id, "awaiting": True})
            break

        executor = TOOL_EXECUTORS.get(step.tool)
        if executor is None:
            result.status = "HANDED_OFF"
            result.handoff_reason = f"no executor registered for {step.tool}"
            break

        attempt = 0
        while True:
            try:
                outcome = await executor(normalized)
                result.tool_results.append({"tool": step.tool, "ok": True, "summary": outcome.get("summary", "")})
                break
            except ToolError as e:
                attempt += 1
                if attempt > 1:  # MAX_TOOL_RETRIES = 1
                    result.status = "HANDED_OFF"
                    result.handoff_reason = f"tool {step.tool} failed after retry: {e.code}"
                    break
                logger.warning("agent_tool_retry", tool=step.tool, error=e.code)
        if result.handoff_reason:
            break

        result.steps_taken += 1

    if result.status == "RUNNING":
        result.status = "COMPLETED"
        # Read-only runs finish without side effects; the case stays
        # EVIDENCE_READY (RESOLVED is reserved for executed writes).

    await service.finish_agent_run(
        _run_id_or_raise(service, agent_run_id),
        status=result.status,
        steps_taken=result.steps_taken,
        tokens_used=result.tokens_used,
        handoff_reason=result.handoff_reason,
    )
    return result


def _run_id_or_raise(service: HRCaseService, agent_run_id: str | None) -> str:
    if agent_run_id is None:
        raise NotFoundError("Agent run", "None")
    return agent_run_id


async def execute_approved_write(
    service: HRCaseService,
    case_id: str,
    approval_id: str,
    request_id: str,
    executor: ToolExecutor,
) -> dict:
    """Run an approved write tool — the SECOND request after human approval.

    Transaction discipline for crash consistency:
      1. CLAIM is committed FIRST: approval → CONSUMED and the execution row →
         RUNNING are durable before any external side effect runs.
      2. The external ``executor`` then runs OUTSIDE any then-open transaction
         (no long database lock is held during the side effect).
      3. COMPLETION/FAILURE is committed in a SECOND transaction.

    If the process crashes after the external side effect but before the
    completion commit, the CONSUMED approval (durable from step 1) blocks a
    retry from re-executing the side effect — the approval cannot be claimed
    again, so the external operation is not repeated.
    """
    from app.data.models.hr_case import ApprovalRequest

    approval = (
        await service.session.execute(
            __import__("sqlalchemy").select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == service.tenant_id
            )
        )
    ).scalars().first()
    if approval is None:
        raise NotFoundError("Approval request", approval_id)

    params = json.loads(approval.params_json)
    normalized = validate_tool_call(approval.tool_name, params)  # re-validate server-side

    execution = await service.begin_tool_execution(
        case_id,
        approval.tool_name,
        normalized,
        request_id=request_id,
        approval_id=approval_id,
    )
    if execution.status == "SUCCEEDED":
        return {"status": "already_done", "execution_id": execution.id}

    # ---- Step 1: claim is durable BEFORE the external side effect. ----
    await service.transition_case(case_id, case_state.EXECUTING, reason=f"executing {approval.tool_name}")
    await service.session.commit()

    # ---- Step 2: external side effect, with no open DB transaction/lock. ----
    try:
        outcome = await executor(normalized)
    except ToolError as e:
        # ---- Step 3a: failure recorded in a second transaction. ----
        await service.finish_tool_execution(execution.id, ok=False, error_code=e.code, error_message=str(e))
        await service.transition_case(case_id, case_state.FAILED, reason=e.code)
        await service.session.commit()
        return {"status": "failed", "error_code": e.code, "execution_id": execution.id}

    # ---- Step 3b: success recorded in a second transaction. ----
    await service.finish_tool_execution(
        execution.id, ok=True, result_summary=str(outcome.get("summary", ""))[:500]
    )
    await service.transition_case(case_id, case_state.RESOLVED, reason=f"{approval.tool_name} done")
    await service.session.commit()
    return {"status": "done", "execution_id": execution.id, "summary": outcome.get("summary", "")}


def plan_steps_for_run(plan: CasePlanDraft) -> list[PlanStep]:
    """Expose plan steps for observability/eval without leaking internals."""
    return list(plan.steps)
