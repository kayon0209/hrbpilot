"""HRBP AI Workbench — Evaluation dashboard API routes.

GET  /api/eval/metrics           — all scenarios summary
GET  /api/eval/metrics/{id}      — single scenario detail
GET  /api/eval/metrics/{id}/trend — metric trend over N days
POST /api/eval/feedback           — human feedback (handled by feedback.py)
GET  /api/eval/golden/{id}        — golden dataset for scenario
"""

from fastapi import APIRouter, Query

from app.evaluation.golden_dataset import GOLDEN_DATASETS
from app.evaluation.metrics import metrics_aggregator
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/metrics")
async def get_all_metrics():
    """Return aggregated metrics for all scenarios."""
    return {"scenarios": await metrics_aggregator.get_all_scenarios_summary_async()}


@router.get("/metrics/{scenario_id}")
async def get_scenario_metrics(scenario_id: str):
    """Return detailed metrics for a single scenario."""
    metrics = await metrics_aggregator.get_scenario_metrics_async(scenario_id)
    if not metrics:
        return {"scenario_id": scenario_id, "metrics": {}, "message": "No data yet"}
    return {"scenario_id": scenario_id, "metrics": metrics}


@router.get("/metrics/{scenario_id}/trend")
async def get_metric_trend(
    scenario_id: str,
    metric: str = Query(..., description="Metric name, e.g. citation_accuracy"),
    days: int = Query(7, ge=1, le=90, description="Number of days to look back"),
):
    """Return trend data for a specific metric over the last N days."""
    trend = await metrics_aggregator.get_trend_async(scenario_id, metric, window_days=days)
    return {"scenario_id": scenario_id, "metric": metric, "days": days, "data": trend}


@router.get("/golden/{scenario_id}")
async def get_golden_dataset(scenario_id: str):
    """Return golden dataset samples for a scenario."""
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
