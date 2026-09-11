"""Policy QA citation gate tests (Phase 2).

Locks the deterministic retrieval→citation gate over the synthetic corpus:
metric thresholds from the upgrade plan, no-evidence citation clearing, and
harness determinism. Thresholds are gates, not achievements — failing here
means the retrieval/citation layer regressed.
"""

import pytest

from app.evaluation.policy_qa_citation_eval import build_chunk_rows, evaluate_policy_qa, retrieve_deterministic


@pytest.fixture(scope="module")
def eval_out():
    return evaluate_policy_qa(top_k=5)


def test_gate_source_recall(eval_out):
    assert eval_out["summary"]["avg_source_recall"] >= 0.80


def test_gate_source_precision(eval_out):
    assert eval_out["summary"]["avg_source_precision"] >= 0.85


def test_gate_citation_completeness(eval_out):
    assert eval_out["summary"]["avg_completeness"] == 1.0


def test_sample_accounting(eval_out):
    s = eval_out["summary"]
    assert s["total_samples"] == 50
    assert s["scored_samples"] == 45  # 5 injection-refusal samples are N/A
    counts = s["error_class_counts"]
    assert sum(counts.values()) == 50
    assert counts["no_expectation"] == 5
    # Production binding fix: losing a retrieved doc at binding stage is a bug.
    assert counts["binding_loss"] == 0


def test_refusal_samples_excluded_from_citation_scores(eval_out):
    for r in eval_out["results"]:
        if r.error_class == "no_expectation":
            assert r.source_recall is None
            assert r.source_precision is None


def test_mode_is_offline_deterministic(eval_out):
    assert eval_out["summary"]["mode"] == "OFFLINE-DETERMINISTIC"


def test_harness_is_deterministic():
    first = evaluate_policy_qa(top_k=5)
    second = evaluate_policy_qa(top_k=5)
    assert first["summary"] == second["summary"]
    assert [r.source_recall for r in first["results"]] == [r.source_recall for r in second["results"]]


def test_retrieval_ranks_stable():
    rows = build_chunk_rows()
    a = [r.chunk_id for r in retrieve_deterministic("年假没休完能顺延到明年吗？", rows, top_k=5)]
    b = [r.chunk_id for r in retrieve_deterministic("年假没休完能顺延到明年吗？", rows, top_k=5)]
    assert a == b
