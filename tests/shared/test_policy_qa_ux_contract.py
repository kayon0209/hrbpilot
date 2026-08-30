"""User-facing policy answers have a stable action-oriented structure."""

from pathlib import Path


def test_policy_answer_prompt_requires_business_structure():
    prompt = Path("app/scenarios/policy_qa/prompts/policy_qa.txt").read_text(encoding="utf-8")

    for heading in ("结论", "适用条件", "制度依据", "不确定项", "下一步"):
        assert heading in prompt

    assert "不得把未找到依据和引用标记同时写在一份回答中" in prompt


def test_no_evidence_answer_keeps_action_structure_without_fake_citation():
    from app.scenarios.policy_qa.postprocessors import NO_EVIDENCE_TEMPLATE

    for heading in ("结论", "不确定项", "下一步"):
        assert heading in NO_EVIDENCE_TEMPLATE
    assert "【引用:" not in NO_EVIDENCE_TEMPLATE
