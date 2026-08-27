"""HR Case API routes (Phase 5).

Endpoints (approve and execute are TWO separate requests — never one):
  POST /api/v1/hr-cases                 create a case
  GET  /api/v1/hr-cases/{id}            case detail
  POST /api/v1/hr-cases/{id}/plan       agent plan (LLM → validated, stored)
  POST /api/v1/hr-cases/{id}/clarify    answer a clarification, back to TRIAGED
  POST /api/v1/hr-cases/{id}/approve    human decision on a pending approval
  POST /api/v1/hr-cases/{id}/execute    run an APPROVED write (separate call)
  GET  /api/v1/hr-cases/{id}/events     append-only audit trail

Security: operator identity comes from the auth context, never the body;
every service call is tenant-scoped.
"""

import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.scenarios.hr_case_agent import state as case_state
from app.scenarios.hr_case_agent.agent_loop import execute_approved_write, run_plan
from app.scenarios.hr_case_agent.planner import Planner, requires_human_review
from app.scenarios.hr_case_agent.service import HRCaseService
from app.shared.errors import AppError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/hr-cases", tags=["hr-cases"])


class CreateCaseBody(BaseModel):
    subject_ref: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    risk_level: str = Field("LOW", pattern="^(LOW|MEDIUM|HIGH)$")


class PlanBody(BaseModel):
    goal: str = Field(..., min_length=1, max_length=2000)
    llm_proposal: str | None = Field(
        None, description="Optional LLM JSON proposal; when absent, a deterministic triage plan is used"
    )


class ClarifyBody(BaseModel):
    answer: str = Field(..., min_length=1, max_length=4000)


class ApproveBody(BaseModel):
    approval_id: str = Field(..., min_length=1)
    decision: str = Field(..., pattern="^(approve|reject)$")
    reason: str | None = Field(None, max_length=1000)


class ExecuteBody(BaseModel):
    approval_id: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1, max_length=64)


def _service(request: Request, session: AsyncSession) -> HRCaseService:
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    role = getattr(request.state, "user_role", "employee")
    return HRCaseService(session, tenant_id, actor=f"user:{user_id}|role:{role}")


@router.post("")
@require_auth
async def create_case(body: CreateCaseBody, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    case = await service.create_case(
        created_by=getattr(request.state, "user_id", "unknown"),
        subject_ref=body.subject_ref,
        category=body.category,
        title=body.title,
        description=body.description,
        risk_level=body.risk_level,
    )
    await session.commit()
    return {"case_id": case.id, "status": case.status, "risk_level": case.risk_level}


@router.get("/{case_id}")
@require_auth
async def get_case(case_id: str, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    case = await service.get_case(case_id)
    return {
        "case_id": case.id,
        "status": case.status,
        "risk_level": case.risk_level,
        "category": case.category,
        "title": case.title,
        "owner_id": case.owner_id,
        "subject_ref": case.subject_ref,
    }


def _default_plan_proposal(case) -> str:
    """Deterministic triage plan when no LLM proposal is supplied."""
    import json

    if requires_human_review(case.category, case.risk_level):
        return json.dumps(
            {
                "steps": [{"tool": "search_policy", "params": {"query": case.title}, "reason": "gather policy evidence"}],
                "rationale": "high-risk category: evidence-only plan, human handoff required",
                "risk_notes": f"category={case.category} risk={case.risk_level}",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "steps": [{"tool": "search_policy", "params": {"query": case.title}, "reason": "gather policy evidence"}],
            "rationale": "deterministic evidence-only plan",
        },
        ensure_ascii=False,
    )


@router.post("/{case_id}/plan")
@require_auth
async def plan_case(case_id: str, body: PlanBody, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    case = await service.get_case(case_id)
    if requires_human_review(case.category, case.risk_level):
        # high risk: run evidence gathering then hand off — no write approval
        proposal = _default_plan_proposal(case)
    else:
        proposal = body.llm_proposal or _default_plan_proposal(case)

    planner = Planner(None)
    draft = planner.validate(proposal)  # server-side re-validation

    run = await service.start_agent_run(case_id, body.goal)
    plan_row = await service.save_plan(
        case_id,
        steps=[{"tool": s.tool, "params": s.params, "reason": s.reason, "expected": s.expected} for s in draft.steps],
        rationale=draft.rationale,
        risk_notes=draft.risk_notes,
        agent_run_id=run.id,
    )
    await session.commit()
    return {
        "plan_id": plan_row.id,
        "agent_run_id": run.id,
        "steps": [s.tool for s in draft.steps],
        "human_review_required": requires_human_review(case.category, case.risk_level),
    }


@router.post("/{case_id}/clarify")
@require_auth
async def clarify_case(case_id: str, body: ClarifyBody, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    case = await service.get_case(case_id)
    if case.status != case_state.NEEDS_CLARIFICATION:
        raise AppError(f"Case is {case.status}, clarification answers apply to NEEDS_CLARIFICATION", code="INVALID_STATE", status_code=409)
    await service.transition_case(case_id, case_state.TRIAGED, reason="clarification answered")
    await session.commit()
    return {"case_id": case_id, "status": case_state.TRIAGED}


@router.post("/{case_id}/run")
@require_auth
async def run_case_plan(case_id: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Execute the current plan: reads run now; a write step stops for approval."""
    service = _service(request, session)
    plan_row = await service.get_case(case_id)
    from sqlalchemy import select

    from app.data.models.hr_case import CasePlan

    plan = (
        await session.execute(
            select(CasePlan).where(CasePlan.case_id == case_id, CasePlan.tenant_id == service.tenant_id).order_by(CasePlan.created_at.desc())
        )
    ).scalars().first()
    if plan is None:
        raise NotFoundError("Case plan", case_id)

    from app.scenarios.hr_case_agent.planner import Planner

    steps_payload = json.dumps({"steps": json.loads(plan.steps_json)}, ensure_ascii=False)
    draft = Planner(None).validate(steps_payload)
    run = await service.start_agent_run(case_id, plan_row.title)
    outcome = await run_plan(service, case_id, draft, agent_run_id=run.id)
    await session.commit()
    return {
        "run_id": run.id,
        "status": outcome.status,
        "steps_taken": outcome.steps_taken,
        "handoff_reason": outcome.handoff_reason,
        "approval_id": outcome.approval_id,
        "tool_results": outcome.tool_results,
    }


@router.post("/{case_id}/approve")
@require_auth
async def approve_case(case_id: str, body: ApproveBody, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    role = getattr(request.state, "user_role", "employee")
    user_id = getattr(request.state, "user_id", "unknown")
    approval = await service.decide_approval(body.approval_id, approver_id=user_id, decision=body.decision, reason=body.reason, role=role)
    if body.decision == "reject":
        await service.transition_case(case_id, case_state.PLAN_READY, reason="approval rejected")
    await session.commit()
    return {"approval_id": approval.id, "status": approval.status}


@router.post("/{case_id}/execute")
@require_auth
async def execute_case(case_id: str, body: ExecuteBody, request: Request, session: AsyncSession = Depends(get_db)):
    """SECOND request after explicit approval — runs the approved write tool."""
    # Role gate FIRST: employees never reach the database path.
    role = getattr(request.state, "user_role", "employee")
    if role not in ("hr_manager", "admin"):
        from app.scenarios.hr_case_agent.service import CasePermissionDeniedError

        raise CasePermissionDeniedError(f"Role {role} cannot execute write tools")
    service = _service(request, session)

    from sqlalchemy import select

    from app.data.models.hr_case import ApprovalRequest

    approval = (
        await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.id == body.approval_id, ApprovalRequest.tenant_id == service.tenant_id)
        )
    ).scalars().first()
    if approval is None:
        raise NotFoundError("Approval request", body.approval_id)

    from app.scenarios.hr_case_agent.agent_loop import TOOL_EXECUTORS
    from app.scenarios.hr_case_agent.tools import ToolError

    real_executor = TOOL_EXECUTORS.get(approval.tool_name)
    if real_executor is None:
        # No production executor wired for this tool yet: record intent, keep
        # case AWAITING_APPROVAL-consumed state, return explicit error.
        raise AppError(f"No executor registered for {approval.tool_name}", code="TOOL_EXECUTOR_MISSING", status_code=501)

    try:
        outcome = await execute_approved_write(service, case_id, body.approval_id, body.request_id, real_executor)
    except ToolError as e:
        await session.commit()
        raise AppError(str(e), code=e.code, status_code=502) from e
    await session.commit()
    return outcome


@router.get("/{case_id}/events")
@require_auth
async def case_events(case_id: str, request: Request, session: AsyncSession = Depends(get_db)):
    service = _service(request, session)
    events = await service.list_events(case_id)
    return {
        "events": [
            {"seq": e.seq, "type": e.event_type, "actor": e.actor, "payload": json_loads_safe(e.payload_json), "at": e.created_at.isoformat() if e.created_at else None}
            for e in events
        ]
    }


def json_loads_safe(raw: str | None) -> dict:
    import json

    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"raw": raw}


@router.get("/{case_id}/runs/{run_id}")
@require_auth
async def get_agent_run_trace(case_id: str, run_id: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Observability: full trace of one agent run (Phase 7).

    Returns the run record, its plan, tool executions, approval decisions,
    and the slice of case events belonging to this run — everything needed
    to reconstruct what the agent did and why.
    """
    service = _service(request, session)
    await service.get_case(case_id)  # tenant check

    from sqlalchemy import select

    from app.data.models.hr_case import AgentRun, ApprovalRequest, CasePlan, ToolExecution

    run = (
        await session.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.case_id == case_id, AgentRun.tenant_id == service.tenant_id)
        )
    ).scalars().first()
    if run is None:
        raise NotFoundError("Agent run", run_id)

    plan = (
        await session.execute(
            select(CasePlan).where(CasePlan.agent_run_id == run_id, CasePlan.tenant_id == service.tenant_id).order_by(CasePlan.created_at.desc())
        )
    ).scalars().first()

    executions = (
        await session.execute(
            select(ToolExecution).where(ToolExecution.agent_run_id == run_id, ToolExecution.tenant_id == service.tenant_id).order_by(ToolExecution.created_at.asc())
        )
    ).scalars().all()

    approvals = (
        await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.case_id == case_id, ApprovalRequest.tenant_id == service.tenant_id).order_by(ApprovalRequest.created_at.asc())
        )
    ).scalars().all()

    events = await service.list_events(case_id)
    run_events = [e for e in events if e.agent_run_id == run_id]

    return {
        "run": {
            "id": run.id,
            "goal": run.goal,
            "status": run.status,
            "steps_taken": run.steps_taken,
            "tokens_used": run.tokens_used,
            "handoff_reason": run.handoff_reason,
        },
        "plan": {
            "id": plan.id if plan else None,
            "steps": json.loads(plan.steps_json) if plan else [],
            "rationale": plan.rationale if plan else None,
            "risk_notes": plan.risk_notes if plan else None,
        },
        "tool_executions": [
            {
                "tool": ex.tool_name,
                "request_id": ex.request_id,
                "status": ex.status,
                "result_summary": ex.result_summary,
                "error_code": ex.error_code,
                "attempt": ex.attempt,
            }
            for ex in executions
        ],
        "approvals": [
            {
                "id": ap.id,
                "tool": ap.tool_name,
                "status": ap.status,
                "approver_id": ap.approver_id,
                "decision_reason": ap.decision_reason,
            }
            for ap in approvals
        ],
        "events": [
            {"seq": e.seq, "type": e.event_type, "actor": e.actor, "payload": json_loads_safe(e.payload_json)}
            for e in run_events
        ],
    }
