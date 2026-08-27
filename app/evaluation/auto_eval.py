"""HRBP AI Workbench — Auto evaluation service.

Metrics: Faithfulness, Answer Relevance, Citation Accuracy.
Runs asynchronously after each request — never blocks response.

Evaluation strategy: LLM-as-judge. Each metric sends a structured prompt
to the active LLM provider and parses a numeric score from the response.

Score semantics (Phase 1.2):
  - A successfully judged metric yields its real ``float``, including a
    legitimate ``0.0`` (e.g. answer with no citations).
  - A judge call that fails (exception, timeout, no content) or returns an
    unparseable text yields ``None`` — the metric is recorded in
    ``skipped_metrics`` instead of a fabricated ``0.0``, so quality trends
    are never polluted by evaluation-infrastructure failures.
"""

import re
from dataclasses import dataclass, field

from app.evaluation.metrics import metrics_aggregator
from app.shared.logger import get_logger

logger = get_logger(__name__)

_SCORE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")

SKIP_JUDGE_UNAVAILABLE = "judge_unavailable"
SKIP_UNPARSEABLE = "unparseable_judge_output"
SKIP_UNKNOWN_METRIC = "unknown_metric"


@dataclass
class SkippedMetric:
    """A metric that could not be evaluated, with the reason why."""

    metric: str
    reason: str


@dataclass
class EvalOutcome:
    """Result of one evaluation pass.

    ``scores`` contains only successfully judged metrics (real values,
    including 0.0). ``skipped_metrics`` lists everything that could not be
    evaluated and why — never folded into scores.
    """

    scores: dict[str, float] = field(default_factory=dict)
    skipped_metrics: list[SkippedMetric] = field(default_factory=list)


def _truncate(text: str, limit: int = 2000) -> str:
    return text[:limit] if len(text) > limit else text


def _parse_score(raw: str) -> float | None:
    """Extract a 0-1 score from LLM judge output; None if unparseable."""
    m = _SCORE_PATTERN.search(raw)
    if not m:
        return None
    val = float(m.group(1))
    if val > 1.0:
        val = val / 10.0 if val <= 10.0 else val / 100.0
    return round(max(0.0, min(1.0, val)), 4)


async def _llm_judge(prompt: str) -> str | None:
    """Call the active LLM with a judge prompt. Returns None on failure."""
    try:
        from app.rag.llm.orchestrator import get_active_model, get_llm_client

        client = get_llm_client()
        model = get_active_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个严谨的RAG系统评估助手。请根据评分标准给出0到1之间的分数，并简要说明理由。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.0,
            stream=False,
        )
        return response.choices[0].message.content or None
    except Exception as e:
        logger.warning("llm_judge_failed", error=str(e))
        return None


class AutoEvaluator:
    """Evaluate RAG output quality — runs async, non-blocking."""

    async def evaluate(
        self,
        output: str,
        query: str,
        sources: list[dict],
        metrics: list[str],
        tenant_id: str,
        scenario_id: str = "rag_pipeline",
    ) -> EvalOutcome:
        """Run evaluation metrics on a pipeline result.

        Only successfully judged metrics are returned in ``scores`` and
        recorded into the aggregator; failures land in ``skipped_metrics``.
        """
        scores: dict[str, float] = {}
        skipped: list[SkippedMetric] = []

        for metric in metrics:
            if metric == "citation_accuracy":
                value, reason = await self._citation_accuracy(output, sources)
            elif metric == "answer_relevance":
                value, reason = await self._answer_relevance(output, query)
            elif metric == "faithfulness":
                value, reason = await self._faithfulness(output, sources)
            elif metric in (
                "extraction_completeness",
                "topic_coverage",
                "information_completeness",
                "content_diversity",
            ):
                value, reason = await self._generic_completeness(metric, output, sources, query)
            else:
                value, reason = None, SKIP_UNKNOWN_METRIC

            if value is None:
                skipped.append(SkippedMetric(metric=metric, reason=reason or SKIP_JUDGE_UNAVAILABLE))
                logger.warning(
                    "metric_skipped",
                    metric_name=metric,
                    reason=reason,
                    tenant_id=tenant_id,
                    scenario_id=scenario_id,
                )
                continue

            scores[metric] = value

        for metric_name, score in scores.items():
            try:
                await metrics_aggregator.record(tenant_id, scenario_id, metric_name, score)
            except Exception as e:
                logger.warning(
                    "metric_record_failed",
                    metric=metric_name,
                    error=str(e),
                    tenant_id=tenant_id,
                    scenario_id=scenario_id,
                )

        logger.info(
            "evaluation_complete",
            scores=scores,
            skipped=[s.metric for s in skipped],
            tenant_id=tenant_id,
        )
        return EvalOutcome(scores=scores, skipped_metrics=skipped)

    async def _citation_accuracy(self, output: str, sources: list[dict]) -> tuple[float | None, str | None]:
        """Check citations in output against actual sources via LLM judge.

        No sources is a semantic, judgeable fact: the answer cannot carry
        citations, so a real 0.0 is returned instead of a skip.
        """
        if not sources:
            return 0.0, None
        return await self._judge_score(self._citation_prompt(output, sources), "citation_accuracy")

    async def _answer_relevance(self, output: str, query: str) -> tuple[float | None, str | None]:
        """Check if answer is relevant to the query via LLM judge."""
        return await self._judge_score(self._relevance_prompt(output, query), "answer_relevance")

    async def _faithfulness(self, output: str, sources: list[dict]) -> tuple[float | None, str | None]:
        """Check if answer is faithful to sources (no hallucination) via LLM judge.

        Same as citation accuracy: an answer with no sources is a real 0.0.
        """
        if not sources:
            return 0.0, None
        return await self._judge_score(self._faithfulness_prompt(output, sources), "faithfulness")

    async def _generic_completeness(
        self, metric_name: str, output: str, sources: list[dict], query: str
    ) -> tuple[float | None, str | None]:
        """Generic completeness/diversity metric via LLM judge."""
        return await self._judge_score(self._completeness_prompt(metric_name, output, query), metric_name)

    async def _judge_score(self, prompt: str, metric_name: str) -> tuple[float | None, str | None]:
        """Run one judge call; return (score, None) or (None, skip_reason)."""
        raw = await _llm_judge(prompt)
        if raw is None:
            return None, SKIP_JUDGE_UNAVAILABLE
        score = _parse_score(raw)
        if score is None:
            return None, SKIP_UNPARSEABLE
        return score, None

    def _citation_prompt(self, output: str, sources: list[dict]) -> str:
        source_text = "\n---\n".join(f"来源{i}: {_truncate(s.get('content', ''))}" for i, s in enumerate(sources, 1))
        return (
            "评估以下回答中的引用准确性。\n\n"
            f"回答: {_truncate(output)}\n\n"
            f"来源材料:\n{source_text}\n\n"
            "评分标准：1.0=所有引用与来源一致；0.5=部分引用可追溯；0.0=引用与来源不符或无引用。\n"
            "请先给出分数，再说明理由。"
        )

    def _relevance_prompt(self, output: str, query: str) -> str:
        return (
            "评估以下回答与问题的相关性。\n\n"
            f"问题: {query}\n\n"
            f"回答: {_truncate(output)}\n\n"
            "评分标准：1.0=完全切题；0.5=部分相关；0.0=不相关。\n"
            "请先给出分数，再说明理由。"
        )

    def _faithfulness_prompt(self, output: str, sources: list[dict]) -> str:
        source_text = "\n---\n".join(f"来源{i}: {_truncate(s.get('content', ''))}" for i, s in enumerate(sources, 1))
        return (
            "评估以下回答的忠实度（是否有幻觉）。\n\n"
            f"回答: {_truncate(output)}\n\n"
            f"来源材料:\n{source_text}\n\n"
            "评分标准：1.0=回答完全基于来源；0.5=部分内容有来源支撑；0.0=大量内容无来源支撑。\n"
            "请先给出分数，再说明理由。"
        )

    def _completeness_prompt(self, metric_name: str, output: str, query: str) -> str:
        return (
            f"评估以下回答在 '{metric_name}' 维度的质量。\n\n"
            f"问题: {query}\n\n"
            f"回答: {_truncate(output)}\n\n"
            "评分标准：1.0=优秀；0.5=一般；0.0=差。\n"
            "请先给出分数，再说明理由。"
        )
