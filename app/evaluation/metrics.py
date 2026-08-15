"""HRBP AI Workbench — Evaluation metrics aggregation and dashboard API.

Provides:
  - Metrics aggregation per scenario (running averages, trends)
  - Dashboard API endpoints for the evaluation page
  - Golden dataset management stubs (Phase 05 reference implementation)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class MetricEntry:
    scenario_id: str
    metric: str
    score: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class MetricsAggregator:
    """Aggregates evaluation results and computes statistics per scenario."""

    def __init__(self) -> None:
        self._entries: list[MetricEntry] = []

    def record(self, scenario_id: str, metric: str, score: float) -> None:
        self._entries.append(
            MetricEntry(scenario_id=scenario_id, metric=metric, score=score)
        )

    def get_scenario_metrics(self, scenario_id: str) -> dict:
        """Return aggregated metrics for a single scenario."""
        scenario_entries = [e for e in self._entries if e.scenario_id == scenario_id]
        if not scenario_entries:
            return {}

        metrics_by_name: dict[str, list[float]] = {}
        for entry in scenario_entries:
            metrics_by_name.setdefault(entry.metric, []).append(entry.score)

        result: dict = {}
        for metric_name, scores in metrics_by_name.items():
            result[metric_name] = {
                "avg": round(sum(scores) / len(scores), 4),
                "min": round(min(scores), 4),
                "max": round(max(scores), 4),
                "count": len(scores),
                "latest": round(scores[-1], 4),
            }
        return result

    def get_all_scenarios_summary(self) -> list[dict]:
        """Return summary for all scenarios."""
        scenarios = {e.scenario_id for e in self._entries}
        return [
            {
                "scenario_id": sid,
                "metrics": self.get_scenario_metrics(sid),
                "total_entries": len(
                    [e for e in self._entries if e.scenario_id == sid]
                ),
            }
            for sid in sorted(scenarios)
        ]

    def get_trend(
        self, scenario_id: str, metric: str, window_days: int = 7
    ) -> list[dict]:
        """Return metric trend over the last N days."""
        cutoff = datetime.now() - timedelta(days=window_days)
        relevant = [
            e
            for e in self._entries
            if e.scenario_id == scenario_id
            and e.metric == metric
            and datetime.fromisoformat(e.timestamp) >= cutoff
        ]
        relevant.sort(key=lambda e: e.timestamp)
        return [
            {"timestamp": e.timestamp, "score": round(e.score, 4)} for e in relevant
        ]


# Singleton instance
metrics_aggregator = MetricsAggregator()
