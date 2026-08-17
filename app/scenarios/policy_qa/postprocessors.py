"""HRBP AI Workbench — Policy QA PostProcessor: No-Evidence Fallback.

When retrieval returns no chunks, replace the LLM output with a clear
"no evidence found" message instead of letting the LLM fabricate an answer.

The retriever exposes a separate calibrated ``confidence`` value. RRF score is
used only for ranking and is never treated as evidence probability.
"""

from app.config.settings import settings
from app.rag.config_loader import ScenarioConfig
from app.shared.logger import get_logger

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
    """Post-process the pipeline output with a no-evidence check.

    Return the fallback when recall is empty or every hit is below the configured
    evidence threshold.
    """
    if not context:
        logger.info("postprocessor_no_evidence", reason="no_context")
        return NO_EVIDENCE_TEMPLATE

    best_confidence = max(float(chunk.get("confidence", 0.0)) for chunk in context)
    if best_confidence < settings.guardrail_confidence_threshold:
        logger.info(
            "postprocessor_no_evidence",
            reason="low_confidence",
            confidence=best_confidence,
        )
        return NO_EVIDENCE_TEMPLATE

    logger.info("postprocessor_evidence_ok", chunks=len(context))
    return output
