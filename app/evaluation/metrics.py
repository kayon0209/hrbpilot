"""HRBP AI Workbench — Evaluation metrics aggregation and dashboard API.

Primary store is the ``eval_results`` PostgreSQL table (survives restarts,
shared across workers). Falls back to in-memory tracking when the DB is
unavailable so dev mode keeps working.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.shared.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MetricEntry:
    scenario_id: str
    metric: str
    score: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class MetricsAggregator:
    """Aggregates evaluation results and computes statistics per scenario.

    All ``record`` / ``get_*`` methods are async — they persist to PostgreSQL
    when the DB is available and fall back to in-memory when it isn't.
    """

    def __init__(self) -> None:
        self._entries: list[MetricEntry] = []

    async def record(self, tenant_id: str, scenario_id: str, metric: str, score: float) -> None:
        """Record a metric — persists to PostgreSQL, falls back to memory."""
        self._entries.append(MetricEntry(scenario_id=scenario_id, metric=metric, score=score))
        try:
            from app.data.database import get_db_session
            from app.data.models.infra import EvalResult

            async for db in get_db_session(tenant_id):
                db.add(
                    EvalResult(
                        tenant_id=tenant_id,
                        scenario_id=scenario_id,
                        metric=metric,
                        score=score,
                    )
                )
                await db.commit()
        except Exception as e:
            logger.debug("metrics_db_unavailable_using_memory", error=str(e))

    def get_scenario_metrics(self, scenario_id: str) -> dict:
        """Return aggregated metrics for a single scenario (in-memory)."""
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

    async def get_scenario_metrics_async(self, scenario_id: str) -> dict:
        """Return aggregated metrics from PostgreSQL, falling back to memory."""
        try:
            from sqlalchemy import func, select

            from app.data.database import get_db_session
            from app.data.models.infra import EvalResult

            async for db in get_db_session():
                rows = (
                    await db.execute(
                        select(
                            EvalResult.metric,
                            func.avg(EvalResult.score).label("avg"),
                            func.min(EvalResult.score).label("min"),
                            func.max(EvalResult.score).label("max"),
                            func.count(EvalResult.id).label("count"),
                        )
                        .where(EvalResult.scenario_id == scenario_id)
                        .group_by(EvalResult.metric)
                    )
                ).all()
                if not rows:
                    return self.get_scenario_metrics(scenario_id)

                # Fetch latest scores per metric
                latest_rows = (
                    await db.execute(
                        select(EvalResult.metric, EvalResult.score)
                        .where(EvalResult.scenario_id == scenario_id)
                        .order_by(EvalResult.created_at.desc())
                    )
                ).all()
                latest_map: dict[str, float] = {}
                for m, s in latest_rows:
                    if m not in latest_map:
                        latest_map[m] = float(s)

                result: dict = {}
                for row in rows:
                    result[row[0]] = {
                        "avg": round(float(row[1]), 4),
                        "min": round(float(row[2]), 4),
                        "max": round(float(row[3]), 4),
                        "count": int(row[4]),
                        "latest": round(latest_map.get(row[0], float(row[1])), 4),
                    }
                return result
            # No DB session available — fall back to in-memory metrics.
            return self.get_scenario_metrics(scenario_id)
        except Exception as e:
            logger.debug("metrics_db_unavailable_using_memory", error=str(e))
            return self.get_scenario_metrics(scenario_id)

    async def get_all_scenarios_summary_async(self) -> list[dict]:
        """Return summary for all scenarios from PostgreSQL."""
        try:
            from sqlalchemy import func, select

            from app.data.database import get_db_session
            from app.data.models.infra import EvalResult

            async for db in get_db_session():
                scenario_ids = (await db.execute(select(EvalResult.scenario_id).distinct())).scalars().all()
                results = []
                for sid in sorted(scenario_ids):
                    metrics = await self.get_scenario_metrics_async(sid)
                    count_rows = (
                        await db.execute(select(func.count(EvalResult.id)).where(EvalResult.scenario_id == sid))
                    ).scalar()
                    results.append(
                        {
                            "scenario_id": sid,
                            "metrics": metrics,
                            "total_entries": int(count_rows or 0),
                        }
                    )
                return results
            # No DB session available — fall back to in-memory summary.
            return self.get_all_scenarios_summary()
        except Exception as e:
            logger.debug("metrics_db_unavailable_using_memory", error=str(e))
            return self.get_all_scenarios_summary()

    def get_all_scenarios_summary(self) -> list[dict]:
        """Return summary for all scenarios (in-memory)."""
        scenarios = {e.scenario_id for e in self._entries}
        return [
            {
                "scenario_id": sid,
                "metrics": self.get_scenario_metrics(sid),
                "total_entries": len([e for e in self._entries if e.scenario_id == sid]),
            }
            for sid in sorted(scenarios)
        ]

    async def get_trend_async(self, scenario_id: str, metric: str, window_days: int = 7) -> list[dict]:
        """Return metric trend over the last N days from PostgreSQL."""
        try:
            from sqlalchemy import select

            from app.data.database import get_db_session
            from app.data.models.infra import EvalResult

            cutoff = datetime.now() - timedelta(days=window_days)
            async for db in get_db_session():
                rows = (
                    await db.execute(
                        select(EvalResult.created_at, EvalResult.score)
                        .where(
                            EvalResult.scenario_id == scenario_id,
                            EvalResult.metric == metric,
                            EvalResult.created_at >= cutoff,
                        )
                        .order_by(EvalResult.created_at.asc())
                    )
                ).all()
                return [
                    {"timestamp": row[0].isoformat() if row[0] else None, "score": round(float(row[1]), 4)}
                    for row in rows
                ]
            # No DB session available — fall back to in-memory trend.
            return self.get_trend(scenario_id, metric, window_days)
        except Exception as e:
            logger.debug("metrics_db_unavailable_using_memory", error=str(e))
            return self.get_trend(scenario_id, metric, window_days)

    def get_trend(self, scenario_id: str, metric: str, window_days: int = 7) -> list[dict]:
        """Return metric trend over the last N days (in-memory)."""
        cutoff = datetime.now() - timedelta(days=window_days)
        relevant = [
            e
            for e in self._entries
            if e.scenario_id == scenario_id and e.metric == metric and datetime.fromisoformat(e.timestamp) >= cutoff
        ]
        relevant.sort(key=lambda e: e.timestamp)
        return [{"timestamp": e.timestamp, "score": round(e.score, 4)} for e in relevant]


# Singleton instance
metrics_aggregator = MetricsAggregator()
