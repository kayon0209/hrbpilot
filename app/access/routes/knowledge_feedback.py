"""Knowledge feedback routes — the manager's 知识与反馈 action center (spec §7.7)."""

from fastapi import APIRouter, Request

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.knowledge_feedback.service import DecideBody, collect_candidates, decide_candidate

router = APIRouter(prefix="/api/knowledge-feedback", tags=["knowledge-feedback"])


@router.get("/candidates")
@require_auth
async def list_candidates(request: Request):
    """Suggested knowledge-gap candidates with stable IDs.

    AI never auto-confirms a candidate — the status stays ``open`` until a
    human (hr_manager, gated by the knowledge_feedback capability in
    RBACMiddleware) decides (spec §7.7).
    """
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    candidates = await collect_candidates(tenant_id, user_id, user_role)
    return {"candidates": [c.model_dump() for c in candidates]}


@router.post("/candidates/decide")
@require_auth
async def decide(request: Request, body: DecideBody):
    """Confirm / assign / reject a candidate — the only way one closes."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    decided = await decide_candidate(tenant_id, user_id, user_role, body)
    return decided.model_dump()
