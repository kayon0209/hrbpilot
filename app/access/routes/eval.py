"""HRBP AI Workbench — Evaluation dashboard API routes.

GET  /api/eval/metrics           — all scenarios summary
GET  /api/eval/metrics/{id}      — single scenario detail
GET  /api/eval/metrics/{id}/trend — metric trend over N days
GET  /api/eval/golden/{id}        — golden dataset for scenario
"""

from fastapi import APIRouter, Query, Request

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.evaluation.golden_dataset import GOLDEN_DATASETS
from app.evaluation.metrics import metrics_aggregator
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/metrics")
@require_auth
@require_role("hr_manager")
async def get_all_metrics(request: Request):
    """Return aggregated metrics for all scenarios."""
    tenant_id = require_tenant_id(request)
    return {"scenarios": await metrics_aggregator.get_all_scenarios_summary_async(tenant_id)}


@router.get("/metrics/{scenario_id}")
@require_auth
@require_role("hr_manager")
async def get_scenario_metrics(scenario_id: str, request: Request):
    """Return detailed metrics for a single scenario."""
    tenant_id = require_tenant_id(request)
    metrics = await metrics_aggregator.get_scenario_metrics_async(tenant_id, scenario_id)
    if not metrics:
        return {"scenario_id": scenario_id, "metrics": {}, "message": "No data yet"}
    return {"scenario_id": scenario_id, "metrics": metrics}


@router.get("/metrics/{scenario_id}/trend")
@require_auth
@require_role("hr_manager")
async def get_metric_trend(
    scenario_id: str,
    request: Request,
    metric: str = Query(..., description="Metric name, e.g. citation_accuracy"),
    days: int = Query(7, ge=1, le=90, description="Number of days to look back"),
):
    """Return trend data for a specific metric over the last N days."""
    tenant_id = require_tenant_id(request)
    trend = await metrics_aggregator.get_trend_async(tenant_id, scenario_id, metric, window_days=days)
    return {"scenario_id": scenario_id, "metric": metric, "days": days, "data": trend}


@router.get("/golden/{scenario_id}")
@require_auth
@require_role("hr_manager")
async def get_golden_dataset(scenario_id: str, request: Request):
    """Return golden dataset samples for a scenario."""
    require_tenant_id(request)
    samples = GOLDEN_DATASETS.get(scenario_id, [])
    return {
        "scenario_id": scenario_id,
        "count": len(samples),
        "samples": [
            {
                "input": s.input,
                "expected_contains": s.expected_output_contains,
                "should_reject": s.should_reject,
                "notes": s.notes,
            }
            for s in samples
        ],
    }
