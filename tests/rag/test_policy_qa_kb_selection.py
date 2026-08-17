"""Policy QA must query the caller-selected real knowledge base."""

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


async def test_citation_confidence_is_bounded_for_sparse_native_score() -> None:
    orchestrator, _ = _orchestrator(score=7.5)

    response = await orchestrator.execute("怎么请假？", tenant_id="tenant-a", user_id="user-a", kb_id="real-kb-uuid")

    assert response.confidence == 1.0
    assert response.citations[0].confidence == 1.0
