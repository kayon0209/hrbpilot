"""Work summary routes — 今日工作 aggregation API (spec §5.1, Phase 2)."""

from fastapi import APIRouter, Request

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.work_summary.service import collect_work_summaries

router = APIRouter(prefix="/api/work-summaries", tags=["work-summaries"])


@router.get("")
@require_auth
async def get_work_summaries(request: Request):
    """Aggregated recent work for the signed-in user: continue / attention / completed_today."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    summaries = await collect_work_summaries(tenant_id, user_id, user_role)
    return summaries.model_dump()
