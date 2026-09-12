"""Per-scenario metric applicability and ranking metrics (§7.2).

Why this exists: one metric does not fit every scenario. ``keyword_recall``
measures whether expected phrases appear VERBATIM in the output. That is a
fair test for policy Q&A, where a canonical wording exists, but a broken test
for generative scenarios such as ``culture_content`` — a good generated article
expresses the point in its own words and therefore "misses" the keyword. The
resulting score (e.g. 0.104) reads like poor quality when it actually measures
the metric's own inapplicability.

This module makes that explicit instead of leaving it to the reader:

  * each scenario maps to a KIND (data, so a new scenario is registered by
    editing the table rather than by touching scoring logic);
  * each kind declares the metrics that are fair for it;
  * retrieval scenarios additionally get the ranking metrics they actually
    need — Recall@k and MRR.

Frozen-baseline constraint: existing scores are deliberately left untouched.
This module is used to ANNOTATE them with applicability, not to rewrite the
numbers the frozen baseline reports.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# --- scenario → kind -------------------------------------------------------
# Config-as-data: adding a scenario means adding a row here.
SCENARIO_KINDS: dict[str, str] = {
    "policy_qa": "retrieval",
    "rag_pipeline": "retrieval",
    "culture_content": "generation",
    "weekly_report": "generation",
    "interview_digest": "extraction",
    "voice_insight": "extraction",
}

# Metrics that are a fair measure for each kind.
_KIND_METRICS: dict[str, tuple[str, ...]] = {
    # canonical wording exists, and retrieval order matters
    "retrieval": ("keyword_recall", "citation_recall", "recall_at_k", "mrr", "guardrail_match"),
    # authored prose: verbatim keyword containment is NOT a fair test.
    # Guardrail behaviour and cited sources still are.
    "generation": ("citation_recall", "guardrail_match"),
    # pulling structured facts out of source text: containment is fair
    "extraction": ("keyword_recall", "guardrail_match"),
    "guardrail": ("guardrail_match",),
}


def scenario_kind(scenario_id: str) -> str:
    """Return the kind of a scenario ('retrieval' / 'generation' / ...)."""
    return SCENARIO_KINDS.get(scenario_id, "unknown")


def applicable_metrics(scenario_id: str) -> tuple[str, ...]:
    """Metrics that are a fair measure for this scenario."""
    return _KIND_METRICS.get(scenario_kind(scenario_id), ())


def is_applicable(scenario_id: str, metric: str) -> bool:
    """Whether ``metric`` is a fair measure for ``scenario_id``.

    Unknown scenarios report True: there is no evidence they are unsuitable,
    and silently dropping a metric because a scenario was not registered yet
    would be worse than scoring it.
    """
    allowed = _KIND_METRICS.get(scenario_kind(scenario_id))
    if allowed is None:
        return True
    return metric in allowed


def recall_at_k(
    retrieved: Sequence[str],
    expected: Iterable[str] | None,
    k: int = 5,
) -> float | None:
    """Fraction of expected sources found in the top-k retrieved documents.

    Returns None when the sample carries no expectation — it then imposes no
    retrieval requirement and must not be scored as 0 (that would drag the
    average down for samples that simply have no label).
    """
    # golden samples frequently carry no expectation at all (None or empty)
    expected_set = set() if expected is None else {item for item in expected if item}
    if not expected_set:
        return None
    if k <= 0:
        return 0.0
    hits = len(expected_set & set(list(retrieved)[:k]))
    return round(hits / len(expected_set), 4)


def mrr(retrieved: Sequence[str], expected: Iterable[str] | None) -> float | None:
    """Reciprocal rank of the first relevant document (1/rank, 1.0 = hit@1).

    Returns None when there is no expectation, and 0.0 when nothing relevant
    was retrieved at all.
    """
    expected_set = set() if expected is None else {item for item in expected if item}
    if not expected_set:
        return None
    for rank, doc in enumerate(retrieved, start=1):
        if doc in expected_set:
            return round(1.0 / rank, 4)
    return 0.0
