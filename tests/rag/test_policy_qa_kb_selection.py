"""Policy QA must query the caller-selected real knowledge base."""

import json

from app.rag.config_loader import GuardrailRules, RetrievalStrategy, ScenarioConfig
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator


class _Retriever:
    def __init__(self, score: float = 0.9) -> None:
        self.score = score
        self.kb_id: str | None = None

    async def retrieve(self, **kwargs):
        self.kb_id = kwargs["kb_id"]
        return [
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "kb_id": self.kb_id,
                "source": "员工手册.pdf",
                "section": "请假制度",
                "content": "员工请假需提交申请。",
                "score": self.score,
                "confidence": min(1.0, max(0.0, self.score)),
            }
        ]


class _LLM:
    async def generate(self, **kwargs):
        return "根据制度，需要提交申请。", 12

    async def generate_stream(self, **kwargs):
        yield "根据制度，需要提交申请。"


class _EmptyRetriever:
    async def retrieve(self, **kwargs):
        return []


def _orchestrator(score: float = 0.9) -> tuple[PolicyQAOrchestrator, _Retriever]:
    config = ScenarioConfig(
        scenario_id="policy_qa",
        knowledge_base_id="configured-kb",
        retrieval_strategy=RetrievalStrategy.HYBRID,
        rerank_enabled=False,
        guardrail_rules=GuardrailRules(input=[], output=[]),
        eval_metrics=[],
    )
    orchestrator = PolicyQAOrchestrator(config)
    retriever = _Retriever(score)
    orchestrator.retriever = retriever
    orchestrator.llm = _LLM()
    return orchestrator, retriever


async def test_execute_uses_request_kb_override() -> None:
    orchestrator, retriever = _orchestrator()

    response = await orchestrator.execute("怎么请假？", tenant_id="tenant-a", user_id="user-a", kb_id="real-kb-uuid")

    assert retriever.kb_id == "real-kb-uuid"
    assert response.has_evidence is True
    assert response.citations


async def test_citation_confidence_is_bounded_for_sparse_native_score() -> None:
    orchestrator, _ = _orchestrator(score=7.5)

    response = await orchestrator.execute("怎么请假？", tenant_id="tenant-a", user_id="user-a", kb_id="real-kb-uuid")

    assert response.confidence == 1.0
    assert response.citations[0].confidence == 1.0


async def test_stream_omits_sources_event_when_no_evidence_is_retrieved(monkeypatch) -> None:
    """An empty source list must remain absent, not become the string ``[]`` in history."""
    orchestrator, _ = _orchestrator()
    orchestrator.retriever = _EmptyRetriever()

    async def keep_question(question: str, config: ScenarioConfig) -> str:
        return question

    monkeypatch.setattr("app.scenarios.policy_qa.orchestrator.rewrite_query", keep_question)

    events = [
        json.loads(raw_event)
        async for raw_event in orchestrator.execute_stream(
            "怎么请假？", tenant_id="tenant-a", user_id="user-a", kb_id="real-kb-uuid"
        )
    ]

    assert all(event["event"] != "sources" for event in events)
