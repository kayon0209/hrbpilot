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

# Used when retrieval DID return material but nothing cleared the evidence
# threshold. The citations panel still renders those chunks, so the answer
# must not claim "nothing was found" — it would contradict the sources shown
# directly beneath it. State the real reason instead: material exists, but it
# is not close enough to answer from.
# 文件名占位由 no_evidence_fallback 从 context 的 chunk.source 注入。
# 不写置信度数值：对普通用户那是"数字谜题"，不是"为什么不确定"的解释；
# 用户需要的是"找到了哪些文件、差在哪、接下来怎么办"。
LOW_CONFIDENCE_TEMPLATE = (
    "## 结论\n"
    "找到了一些可能相关的制度材料，但它们与你的问题不够匹配，"
    "暂时不能直接给出确定的答复。\n\n"
    "## 可参考的原文\n"
    "下方「依据与来源」已列出最接近的材料（{source_names}），"
    "请先人工核对它是否适用于你的具体情形，不要把它当作结论直接使用。\n\n"
    "## 下一步\n"
    "1. 换用制度原文中的表述重新提问，或补充制度名称、适用地区与员工类型。\n"
    "2. 核对「依据与来源」中的原文，若确属本情形请提交人工复核。\n"
    "3. 若制度确实未覆盖该情形，再提交制度完善建议。"
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
            "postprocessor_low_confidence",
            reason="low_confidence",
            confidence=best_confidence,
            threshold=settings.guardrail_confidence_threshold,
        )
        # 点名最多 3 个来源文件（去重保序，去扩展名/路径）：让"可参考的原文"
        # 有具体出处，而不是只说"下方已列出"。
        source_names = "、".join(
            dict.fromkeys(
                str(chunk.get("source", "未知来源")).rsplit(".", 1)[0].rsplit("/", 1)[-1] for chunk in context[:3]
            )
        )
        return LOW_CONFIDENCE_TEMPLATE.format(source_names=source_names)

    logger.info("postprocessor_evidence_ok", chunks=len(context))
    return output
