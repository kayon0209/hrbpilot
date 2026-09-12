"""§7.2: per-scenario metric applicability + ranking metrics.

Locks the behaviour that a low score must be interpretable: a metric that is
not a fair test for a scenario is reported as not applicable, rather than
leaving a number like 0.104 to be misread as poor quality.
"""

from app.evaluation.scenario_metrics import (
    applicable_metrics,
    is_applicable,
    mrr,
    recall_at_k,
    scenario_kind,
)


def test_scenario_kinds_separate_retrieval_from_generation():
    assert scenario_kind("policy_qa") == "retrieval"
    assert scenario_kind("culture_content") == "generation"
    assert scenario_kind("interview_digest") == "extraction"


def test_keyword_recall_is_not_applicable_to_generative_scenarios():
    """A generated article uses its own wording, so verbatim containment is not
    a fair measure — this is the root cause of the misleading 0.104."""
    assert is_applicable("culture_content", "keyword_recall") is False
    assert is_applicable("policy_qa", "keyword_recall") is True


def test_guardrail_match_applies_everywhere():
    for sid in ("policy_qa", "culture_content", "interview_digest"):
        assert is_applicable(sid, "guardrail_match") is True


def test_unknown_scenario_is_treated_as_applicable():
    """An unregistered scenario must not silently drop metrics — absence of
    evidence is not evidence of unsuitability."""
    assert scenario_kind("brand_new_scenario") == "unknown"
    assert is_applicable("brand_new_scenario", "keyword_recall") is True


def test_applicable_metrics_lists_ranking_metrics_for_retrieval():
    metrics = applicable_metrics("policy_qa")
    assert "recall_at_k" in metrics
    assert "mrr" in metrics


def test_recall_at_k_counts_expected_sources_in_top_k():
    retrieved = ["docA", "docB", "docC", "docD"]
    assert recall_at_k(retrieved, ["docA", "docD"], k=5) == 1.0
    assert recall_at_k(retrieved, ["docA", "docD"], k=1) == 0.5
    assert recall_at_k(retrieved, ["docZ"], k=5) == 0.0


def test_recall_at_k_returns_none_without_expectation():
    """No expectation means no requirement — scoring 0 would unfairly drag the
    average down for samples that simply have no label."""
    assert recall_at_k(["docA"], [], k=5) is None
    assert recall_at_k(["docA"], None, k=5) is None


def test_recall_at_k_rejects_non_positive_k():
    assert recall_at_k(["docA"], ["docA"], k=0) == 0.0


def test_mrr_rewards_finding_a_relevant_doc_earlier():
    assert mrr(["docA", "docB"], ["docA"]) == 1.0
    assert mrr(["docX", "docB", "docA"], ["docA"]) == round(1 / 3, 4)
    assert mrr(["docX", "docY"], ["docA"]) == 0.0


def test_mrr_returns_none_without_expectation():
    assert mrr(["docA"], []) is None
    assert mrr(["docA"], None) is None
