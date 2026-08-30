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
    "## 结论\n"
    "当前资料中没有找到可以支持明确回答的制度依据。\n\n"
    "## 不确定项\n"
    "现有资料可能未覆盖该情形，不能据此判断具体规则或办理条件。\n\n"
    "## 下一步\n"
    "1. 补充制度名称、适用地区、员工类型或发生时间后重新提问。\n"
    "2. 上传相关制度文件，或将问题交给 HR 人工复核。\n"
    "3. 如果确认制度没有覆盖该情形，再提交制度完善建议。"
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
