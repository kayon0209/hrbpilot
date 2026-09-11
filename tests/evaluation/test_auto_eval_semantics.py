"""AutoEvaluator failure-semantics tests (Phase 1.2).

Locks the boundary between a legitimate quality 0.0 and an evaluation
infrastructure failure: the latter must be skipped, never fabricated into
the scores, and must never pollute the aggregator.
"""

import pytest

from app.evaluation import auto_eval
from app.evaluation.auto_eval import (
    SKIP_JUDGE_UNAVAILABLE,
    SKIP_UNKNOWN_METRIC,
    SKIP_UNPARSEABLE,
    AutoEvaluator,
)

EVALUATOR = AutoEvaluator()


@pytest.fixture()
def recorded(monkeypatch):
    """Capture metrics_aggregator.record calls, bypassing DB/memory writes."""
    calls: list[tuple[str, float]] = []

    async def fake_record(tenant_id: str, scenario_id: str, metric: str, score: float) -> None:
        calls.append((metric, score))

    monkeypatch.setattr(auto_eval.metrics_aggregator, "record", fake_record)
    return calls


def _patch_judge(monkeypatch, reply: str | None) -> None:
    async def fake_judge(prompt: str) -> str | None:
        return reply

    monkeypatch.setattr(auto_eval, "_llm_judge", fake_judge)


async def test_judge_zero_is_a_real_recorded_score(monkeypatch, recorded):
    _patch_judge(monkeypatch, "0 分。理由：回答与来源完全不符。")
    outcome = await EVALUATOR.evaluate(
        output="随便编的内容",
        query="年假怎么休",
        sources=[{"content": "制度正文"}],
        metrics=["faithfulness"],
        tenant_id="t1",
    )
    assert outcome.scores == {"faithfulness": 0.0}
    assert outcome.skipped_metrics == []
    assert recorded == [("faithfulness", 0.0)]


async def test_judge_exception_is_skipped_not_zero(monkeypatch, recorded):
    # _llm_judge imports the client lazily; making get_llm_client raise
    # exercises the real catch-and-skip path inside _llm_judge.
    def broken_client():
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.rag.llm.orchestrator.get_llm_client", broken_client)
    outcome = await EVALUATOR.evaluate(
        output="答案", query="问题", sources=[{"content": "来源"}], metrics=["faithfulness"], tenant_id="t1"
    )
    assert outcome.scores == {}
    assert [s.reason for s in outcome.skipped_metrics] == [SKIP_JUDGE_UNAVAILABLE]
    assert recorded == []


async def test_unparseable_judge_output_is_skipped(monkeypatch, recorded):
    _patch_judge(monkeypatch, "抱歉，我无法评估这段内容。")
    outcome = await EVALUATOR.evaluate(
        output="答案", query="问题", sources=[{"content": "来源"}], metrics=["answer_relevance"], tenant_id="t1"
    )
    assert outcome.scores == {}
    assert [s.reason for s in outcome.skipped_metrics] == [SKIP_UNPARSEABLE]
    assert recorded == []


async def test_no_sources_scores_zero_without_judge(monkeypatch, recorded):
    _patch_judge(monkeypatch, None)  # judge must not even be called
    outcome = await EVALUATOR.evaluate(
        output="无引用的回答", query="问题", sources=[], metrics=["citation_accuracy"], tenant_id="t1"
    )
    assert outcome.scores == {"citation_accuracy": 0.0}
    assert outcome.skipped_metrics == []
    assert recorded == [("citation_accuracy", 0.0)]


async def test_unknown_metric_is_skipped_and_not_aggregated(monkeypatch, recorded):
    _patch_judge(monkeypatch, "0.9 分。")
    outcome = await EVALUATOR.evaluate(
        output="答案", query="问题", sources=[{"content": "来源"}], metrics=["made_up_metric"], tenant_id="t1"
    )
    assert outcome.scores == {}
    assert [s.reason for s in outcome.skipped_metrics] == [SKIP_UNKNOWN_METRIC]
    assert recorded == []


async def test_aggregator_failure_does_not_break_evaluation(monkeypatch, recorded):
    _patch_judge(monkeypatch, "0.8 分。理由：基本切题。")

    async def broken_record(tenant_id: str, scenario_id: str, metric: str, score: float) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(auto_eval.metrics_aggregator, "record", broken_record)
    outcome = await EVALUATOR.evaluate(
        output="答案", query="问题", sources=[{"content": "来源"}], metrics=["faithfulness"], tenant_id="t1"
    )
    assert outcome.scores == {"faithfulness": 0.8}


async def test_mixed_metrics_split_scores_and_skips(monkeypatch, recorded):
    _patch_judge(monkeypatch, "1.0 分。")
    outcome = await EVALUATOR.evaluate(
        output="答案",
        query="问题",
        sources=[{"content": "来源"}],
        metrics=["faithfulness", "answer_relevance", "nope"],
        tenant_id="t1",
    )
    assert outcome.scores == {"faithfulness": 1.0, "answer_relevance": 1.0}
    assert [s.metric for s in outcome.skipped_metrics] == ["nope"]
    assert sorted(recorded) == [("answer_relevance", 1.0), ("faithfulness", 1.0)]
