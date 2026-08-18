from collections.abc import Coroutine

from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import GuardrailRules, ScenarioConfig
from app.rag.pipeline import CapabilityPipeline


class _Retriever:
    async def retrieve(self, **_kwargs):
        return [{"content": "员工每周工作时间为四十小时。", "score": 0.016, "confidence": 0.82}]


async def test_pipeline_uses_calibrated_retrieval_confidence(monkeypatch) -> None:
    def discard_background_task(coro: Coroutine[object, object, object], *, name: str) -> None:
        assert name == "pipeline-audit-log"
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr("app.rag.pipeline._schedule_background_task", discard_background_task)
    config = ScenarioConfig(
        scenario_id="test",
        knowledge_base_id="kb-1",
        guardrail_rules=GuardrailRules(input=[], output=[]),
    )

    result = await CapabilityPipeline(retriever=_Retriever()).execute(
        input="工作时间是多少？",
        config=config,
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert result.confidence == 0.82


def test_citation_verification_handles_chinese_tokens() -> None:
    source = {"content": "公司员工标准每周工作时间为四十小时，特殊安排另行通知。"}

    assert OutputGuardrail()._verify_citations("员工每周工作时间为四十小时。", [source]) is False
