"""Policy QA must query the caller-selected real knowledge base."""

import json

import pytest

from app.rag.config_loader import GuardrailRules, RetrievalStrategy, ScenarioConfig
from app.rag.retrieval.retriever import RetrievalDiagnostics
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator


@pytest.fixture(autouse=True)
def _no_live_llm_rewrite(monkeypatch):
    """本文件测的不是查询改写，因此不该为它调外部 LLM。

    为什么必须是 autouse
    -------------------
    ``rewrite_query`` 的 Step 2 会真的构造 ``LLMOrchestrator`` 并发起网络请求
    （``app/scenarios/policy_qa/preprocessors.py``）。本文件原先只有流式那个测试
    stub 了它，另外两个没有 —— 而那正是本类缺陷的形状：**同一约定只在部分地方落实**。

    后果是实测的：2026-09-16 全量套件两次被拖到超时（SIGTERM / 137），而单跑同一个
    测试只需 3.66 秒。CI 在无外网时同样会挂。

    用 autouse 而不是逐个 monkeypatch，是为了让**以后新增的测试**默认就不带这个
    外部依赖 —— 否则下一个人还会踩同一个坑。
    """

    async def keep_question(question: str, _config) -> str:
        return question

    monkeypatch.setattr("app.scenarios.policy_qa.orchestrator.rewrite_query", keep_question)


def _chunks_for(kb_id: str | None, score: float) -> list[dict]:
    return [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "kb_id": kb_id,
            "source": "员工手册.pdf",
            "section": "请假制度",
            "content": "员工请假需提交申请。",
            "score": score,
            "confidence": min(1.0, max(0.0, score)),
        }
    ]


class _Retriever:
    def __init__(self, score: float = 0.9) -> None:
        self.score = score
        self.kb_id: str | None = None

    async def retrieve(self, **kwargs):
        self.kb_id = kwargs["kb_id"]
        return _chunks_for(self.kb_id, self.score)

    async def retrieve_with_diagnostics(self, **kwargs):
        # policy_qa 现在走带诊断的入口（2026-09-16 降级可见性）；假件跟上即可，
        # 本文件断言的是 kb 选择，与降级无关，所以诊断保持"无降级"。
        return await self.retrieve(**kwargs), RetrievalDiagnostics(strategy="hybrid")


class _LLM:
    async def generate(self, **kwargs):
        return "根据制度，需要提交申请。", 12

    async def generate_stream(self, **kwargs):
        yield "根据制度，需要提交申请。"


class _EmptyRetriever:
    async def retrieve(self, **kwargs):
        return []

    async def retrieve_with_diagnostics(self, **kwargs):
        return [], RetrievalDiagnostics(strategy="hybrid")


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


async def test_stream_omits_sources_event_when_no_evidence_is_retrieved() -> None:
    """An empty source list must remain absent, not become the string ``[]`` in history."""
    orchestrator, _ = _orchestrator()
    orchestrator.retriever = _EmptyRetriever()

    events = [
        json.loads(raw_event)
        async for raw_event in orchestrator.execute_stream(
            "怎么请假？", tenant_id="tenant-a", user_id="user-a", kb_id="real-kb-uuid"
        )
    ]

    assert all(event["event"] != "sources" for event in events)
