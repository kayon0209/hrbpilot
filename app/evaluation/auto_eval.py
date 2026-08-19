"""HRBP AI Workbench — Auto evaluation service.

Metrics: Faithfulness, Answer Relevance, Citation Accuracy.
Runs asynchronously after each request — never blocks response.

Evaluation strategy: LLM-as-judge. Each metric sends a structured prompt
to the active LLM provider and parses a numeric score from the response.
If the LLM call fails or the score cannot be parsed, the metric is skipped
rather than returning a fabricated placeholder.
"""

import re

from app.evaluation.metrics import metrics_aggregator
from app.shared.logger import get_logger

logger = get_logger(__name__)

_SCORE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")
_MAX_RETRIES = 1


def _truncate(text: str, limit: int = 2000) -> str:
    return text[:limit] if len(text) > limit else text


def _parse_score(raw: str) -> float | None:
    """Extract a 0-1 score from LLM judge output."""
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
        from app.rag.llm.orchestrator import get_llm_client, get_active_model

        client = get_llm_client()
        model = get_active_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个严谨的RAG系统评估助手。请根据评分标准给出0到1之间的分数，并简要说明理由。"},
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
        scenario_id: str,
    ) -> dict:
        """Run evaluation metrics on a pipeline result."""
        scores: dict = {}

        for metric in metrics:
            score: float | None = None
            if metric == "citation_accuracy":
                score = await self._citation_accuracy(output, sources)
            elif metric == "answer_relevance":
                score = await self._answer_relevance(output, query)
            elif metric == "faithfulness":
                score = await self._faithfulness(output, sources)
            elif metric in (
                "extraction_completeness",
                "topic_coverage",
                "information_completeness",
                "content_diversity",
            ):
                score = await self._generic_completeness(metric, output, sources, query)
            else:
                logger.warning("unknown_metric", metric=metric)

            if score is None:
                continue
            scores[metric] = score

        for metric_name, score in scores.items():
            try:
                await metrics_aggregator.record(tenant_id, scenario_id, metric_name, score)
            except Exception as e:
                logger.warning("metric_record_failed", metric=metric_name, error=str(e))

        logger.info("evaluation_complete", scores=scores, tenant_id=tenant_id, scenario_id=scenario_id)
        return scores

    async def _citation_accuracy(self, output: str, sources: list[dict]) -> float | None:
        """Check if citations in output match actual sources via LLM judge."""
        if not sources:
            return None
        source_text = "\n---\n".join(
            f"来源{i}: {_truncate(s.get('content', ''))}" for i, s in enumerate(sources, 1)
        )
        prompt = (
            "评估以下回答中的引用准确性。\n\n"
            f"回答: {_truncate(output)}\n\n"
            f"来源材料:\n{source_text}\n\n"
            "评分标准：1.0=所有引用与来源一致；0.5=部分引用可追溯；0.0=引用与来源不符或无引用。\n"
            "请先给出分数，再说明理由。"
        )
        raw = await _llm_judge(prompt)
        if raw is None:
            logger.warning("citation_accuracy_eval_skipped")
            return None
        return _parse_score(raw)

    async def _answer_relevance(self, output: str, query: str) -> float | None:
        """Check if answer is relevant to the query via LLM judge."""
        prompt = (
            "评估以下回答与问题的相关性。\n\n"
            f"问题: {query}\n\n"
            f"回答: {_truncate(output)}\n\n"
            "评分标准：1.0=完全切题；0.5=部分相关；0.0=不相关。\n"
            "请先给出分数，再说明理由。"
        )
        raw = await _llm_judge(prompt)
        if raw is None:
            logger.warning("answer_relevance_eval_skipped")
            return None
        return _parse_score(raw)

    async def _faithfulness(self, output: str, sources: list[dict]) -> float | None:
        """Check if answer is faithful to sources (no hallucination) via LLM judge."""
        if not sources:
            return None
        source_text = "\n---\n".join(
            f"来源{i}: {_truncate(s.get('content', ''))}" for i, s in enumerate(sources, 1)
        )
        prompt = (
            "评估以下回答的忠实度（是否有幻觉）。\n\n"
            f"回答: {_truncate(output)}\n\n"
            f"来源材料:\n{source_text}\n\n"
            "评分标准：1.0=回答完全基于来源；0.5=部分内容有来源支撑；0.0=大量内容无来源支撑。\n"
            "请先给出分数，再说明理由。"
        )
        raw = await _llm_judge(prompt)
        if raw is None:
            logger.warning("faithfulness_eval_skipped")
            return None
        return _parse_score(raw)

    async def _generic_completeness(
        self, metric_name: str, output: str, sources: list[dict], query: str
    ) -> float | None:
        """Generic completeness/diversity metric via LLM judge."""
        prompt = (
            f"评估以下回答在 '{metric_name}' 维度的质量。\n\n"
            f"问题: {query}\n\n"
            f"回答: {_truncate(output)}\n\n"
            "评分标准：1.0=优秀；0.5=一般；0.0=差。\n"
            "请先给出分数，再说明理由。"
        )
        raw = await _llm_judge(prompt)
        if raw is None:
            logger.warning(f"{metric_name}_eval_skipped")
            return None
        return _parse_score(raw)
