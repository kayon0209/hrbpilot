"""制度问答 fallback 文案的用户视角契约（2026-09-13 UX 修复）。

截图实测的用户反馈：低置信度回复把「最高置信度 0.18，要求 0.65」直接写给
用户看——那是开发者校准指标；"可参考的原文"不说参考的是什么文件。本文件
钉住修复后的文案契约，防止回退。
"""

from app.config.settings import settings
from app.rag.config_loader import load_scenario_config
from app.scenarios.policy_qa.postprocessors import no_evidence_fallback


def _chunk(source: str, confidence: float = 0.1) -> dict:
    return {"source": source, "section": "4.2", "content": "片段", "confidence": confidence}


async def test_low_confidence_answer_names_source_files():
    """低置信度回复必须点名来源文件——"参考的是什么"不能让用户去猜。"""
    context = [
        _chunk("制度/差旅报销制度.md"),
        _chunk("制度/差旅报销制度.md"),
        _chunk("员工手册.pdf"),
    ]
    result = await no_evidence_fallback("llm 输出", load_scenario_config("policy_qa"), context)
    assert "差旅报销制度" in result
    assert "员工手册" in result
    # 同名文件只出现一次（去重）
    assert result.count("差旅报销制度") == 1


async def test_low_confidence_answer_hides_calibration_numbers():
    """置信度数值是开发者指标：不得出现在用户可见的正文里。"""
    context = [_chunk("a.md", confidence=0.18)]
    result = await no_evidence_fallback("llm 输出", load_scenario_config("policy_qa"), context)
    assert "0.18" not in result
    assert f"{settings.guardrail_confidence_threshold:.2f}" not in result
    assert "置信度" not in result
    # 但"为什么不确定"仍要用用户话术讲清楚
    assert "不够匹配" in result
