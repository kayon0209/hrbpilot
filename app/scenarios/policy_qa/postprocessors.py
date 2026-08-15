"""HRBP AI Workbench — Policy QA PostProcessor: No-Evidence Fallback.

Per ADR-004: When top-1 retrieval similarity < confidence-threshold (default 0.65),
replace the LLM output with a clear "no evidence found" message.
This prevents hallucination in the policy QA scenario.
"""

from app.rag.config_loader import ScenarioConfig
from app.shared.logger import get_logger
from app.config.settings import settings

logger = get_logger(__name__)

NO_EVIDENCE_TEMPLATE = (
    "⚠️ 未在现有制度中找到与您的问题直接相关的依据。\n\n"
    "建议:\n"
    "1. 换一种方式描述您的问题（如使用更具体的制度术语）\n"
    "2. 联系 HR 部门获取人工解答\n"
    "3. 如果您认为这是制度缺失，可以向 HR 管理层提出制度完善建议"
)


async def no_evidence_fallback(
    output: str,
    config: ScenarioConfig,
    context: list[dict],
) -> str:
    """Post-process the pipeline output with a single confidence threshold check.

    Per ADR-004:
      - If no context chunks retrieved → return "no evidence" message
      - If top-1 similarity < guardrail_confidence_threshold (0.65) → "no evidence"
      - Otherwise → pass through unchanged
    """
    # No context at all
    if not context:
        logger.info("postprocessor_no_evidence", reason="no_context")
        return NO_EVIDENCE_TEMPLATE

    # Single threshold check — matches ADR-004 spec
    top_confidence = max(chunk.get("score", 0.0) for chunk in context)
    threshold = settings.guardrail_confidence_threshold

    if top_confidence < threshold:
        logger.info(
            "postprocessor_no_evidence",
            reason="confidence_below_threshold",
            confidence=round(top_confidence, 4),
            threshold=threshold,
        )
        return NO_EVIDENCE_TEMPLATE

    # Confidence meets threshold — pass through
    logger.info(
        "postprocessor_confidence_ok",
        confidence=round(top_confidence, 4),
    )
    return output
