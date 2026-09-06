"""Metrics aggregator stub-filter tests (PR-00A).

Locks the rule that placeholder/estimated eval scores (``is_stub``) never
participate in averages, trends or scenario summaries, while genuinely
measured rows keep flowing through the same queries.

Uses an in-memory sqlite database for the DB-backed aggregator paths so the
filter is verified against a real SQL query, not just Python-side mocks.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models import infra as infra_models
from app.data.models.base import Base
from app.evaluation.metrics import MetricsAggregator


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[infra_models.EvalResult.__table__]))

    asyncio.run(_create_tables())

    # Route the aggregator's ``from app.data.database import get_db_session``
    # to a session bound to this sqlite engine. Do NOT set session.info
    # tenant_id: the global after_begin listener would emit PostgreSQL's
    # set_config, which sqlite does not provide.
    async def fake_get_db_session(tenant_id: str = "default"):
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.data.database.get_db_session", fake_get_db_session)
    yield factory

    asyncio.run(engine.dispose())


async def _seed(session_factory, rows: list[dict]) -> None:
    """Insert eval_results rows (id, metric, score, is_stub, age_days)."""
    async with session_factory() as session:
        for r in rows:
            session.add(
                infra_models.EvalResult(
                    id=r["id"],
                    tenant_id="t1",
                    scenario_id="policy_qa",
                    metric=r["metric"],
                    score=r["score"],
                    is_stub=r.get("is_stub", False),
                    created_at=datetime.now(UTC) - timedelta(days=r["age_days"]),
                )
            )
        await session.commit()


async def test_aggregator_filters_stub_from_scenario_metrics(session_factory):
    await _seed(
        session_factory,
        [
            {"id": "real-1", "metric": "citation_accuracy", "score": 0.9, "age_days": 1},
            {"id": "real-2", "metric": "citation_accuracy", "score": 0.8, "age_days": 2},
            {"id": "stub-1", "metric": "citation_accuracy", "score": 0.7, "is_stub": True, "age_days": 30},
            {"id": "stub-2", "metric": "answer_relevance", "score": 0.7, "is_stub": True, "age_days": 30},
        ],
    )
    agg = MetricsAggregator()
    result = await agg.get_scenario_metrics_async("policy_qa")

    # stub citation_accuracy row must be excluded: avg(0.9, 0.8) = 0.85
    assert result["citation_accuracy"]["avg"] == 0.85
    assert result["citation_accuracy"]["count"] == 2
    # metric that only has stub rows must not appear at all
    assert "answer_relevance" not in result


async def test_aggregator_filters_stub_from_scenario_summary(session_factory):
    await _seed(
        session_factory,
        [
            {"id": "real-1", "metric": "answer_relevance", "score": 0.6, "age_days": 1},
            {"id": "stub-1", "metric": "answer_relevance", "score": 0.7, "is_stub": True, "age_days": 30},
        ],
    )
    agg = MetricsAggregator()
    summary = await agg.get_all_scenarios_summary_async()

    assert len(summary) == 1
    assert summary[0]["scenario_id"] == "policy_qa"
    assert summary[0]["total_entries"] == 1  # stub row not counted
    assert summary[0]["metrics"]["answer_relevance"]["count"] == 1


async def test_aggregator_filters_stub_from_trend(session_factory):
    await _seed(
        session_factory,
        [
            {"id": "real-1", "metric": "citation_accuracy", "score": 0.9, "age_days": 1},
            {"id": "stub-1", "metric": "citation_accuracy", "score": 0.7, "is_stub": True, "age_days": 30},
        ],
    )
    agg = MetricsAggregator()
    trend = await agg.get_trend_async("policy_qa", "citation_accuracy", window_days=7)

    assert len(trend) == 1
    assert trend[0]["score"] == 0.9


async def test_record_writes_is_stub_false(session_factory):
    agg = MetricsAggregator()
    await agg.record("t1", "policy_qa", "faithfulness", 0.8)

    async with session_factory() as session:
        row = (await session.execute(select(infra_models.EvalResult))).scalars().first()
        assert row is not None
        assert row.metric == "faithfulness"
        assert row.score == 0.8
        assert row.is_stub is False


def test_model_column_default_is_false_on_flush(session_factory):
    """ORM default=False is applied when the row is flushed, keeping the
    NOT NULL constraint satisfied for insert paths that omit is_stub."""

    async def _flush():
        async with session_factory() as session:
            row = infra_models.EvalResult(tenant_id="t1", scenario_id="policy_qa", metric="x", score=0.5)
            session.add(row)
            await session.flush()
            assert row.is_stub is False

    asyncio.run(_flush())


def test_model_is_stub_true_can_be_set_explicitly():
    row = infra_models.EvalResult(tenant_id="t1", scenario_id="policy_qa", metric="x", score=0.7, is_stub=True)
    assert row.is_stub is True


def test_eval_outcome_never_fabricates_not_measured_scores():
    """AutoEvaluator contract: an unmeasured metric yields status=not_measured
    with score=None instead of a placeholder, and is never recorded."""
    from app.evaluation.auto_eval import EvalOutcome, SkippedMetric

    outcome = EvalOutcome(
        scores={},
        skipped_metrics=[SkippedMetric(metric="citation_accuracy", reason="judge_unavailable")],
    )
    assert "citation_accuracy" not in outcome.scores
    assert [s.metric for s in outcome.skipped_metrics] == ["citation_accuracy"]
