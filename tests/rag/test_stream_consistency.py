"""PR-03 stream consistency tests for policy QA.

Locks that the SSE ``done`` event exposes the authoritative terminal answer
(``final_answer``), which may differ from the raw chunk stream when
no-evidence fallback or output guardrails replace the content.
"""

import json
from typing import Any, cast

import pytest

from app.rag.retrieval.retriever import RetrievalDiagnostics
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.scenarios.policy_qa.postprocessors import NO_EVIDENCE_TEMPLATE
from app.scenarios.policy_qa.schemas import QAResponse


@pytest.fixture(autouse=True)
def _no_live_llm_rewrite(monkeypatch):
    """本文件测的是流式与非流式的一致性，与查询改写无关 —— 不该为它调外部 LLM。

    ``rewrite_query`` 的 Step 2 会真的构造 ``LLMOrchestrator`` 并发起网络请求
    （``app/scenarios/policy_qa/preprocessors.py``）。本文件的 ``_orchestrator``
    只替换了 llm / retriever / guardrails，**没有**替换改写步骤，于是每个用例都会
    打一次真实 LLM：延迟一抖，整个测试就会挂住（2026-09-16 全量套件两次被拖到
    超时 SIGTERM），CI 在无外网时同样会挂。

    autouse 是为了让"以后新增的用例"默认不带这个外部依赖。
    """

    async def keep_question(question: str, _config) -> str:
        return question

    monkeypatch.setattr("app.scenarios.policy_qa.orchestrator.rewrite_query", keep_question)


class _LLM:
    async def generate(self, **kwargs):
        return "根据制度，年假可顺延。", 12

    async def generate_stream(self, **kwargs):
        yield "根据制度，"
        yield "年假可顺延。"


class _Retriever:
    def __init__(self, chunks):
        self._chunks = chunks

    async def retrieve(self, **kwargs):
        return self._chunks

    async def retrieve_with_diagnostics(self, **kwargs):
        # policy_qa 现在走带诊断的入口（2026-09-16 降级可见性）；假件必须跟上，
        # 否则测的是一条已经不存在的调用路径。
        return self._chunks, RetrievalDiagnostics(strategy="hybrid")


class _ReplacingOutputGuard:
    async def check(self, _text, _rules, **_kwargs):
        return "安全的最终回答", {"blocked": True}


def _orchestrator(chunks) -> PolicyQAOrchestrator:
    from app.guardrails.input_guard import InputGuardrail
    from app.guardrails.output_guard import OutputGuardrail
    from app.rag.config_loader import load_scenario_config

    orch = PolicyQAOrchestrator.__new__(PolicyQAOrchestrator)
    orch.llm = cast(Any, _LLM())
    orch.retriever = cast(Any, _Retriever(chunks))
    orch.input_guard = InputGuardrail()
    orch.output_guard = OutputGuardrail()
    orch.config = load_scenario_config("policy_qa")
    orch.config.eval_metrics = []
    return orch


@pytest.mark.asyncio
async def test_stream_and_non_stream_return_same_final_answer():
    orch = _orchestrator([{"source": "员工手册.pdf", "section": "4.2", "content": "年假可顺延", "confidence": 0.9}])

    result = await orch.execute("年假能顺延吗？", "t1", "u1", kb_id="kb1")
    assert isinstance(result, QAResponse)
    assert result.answer == "根据制度，年假可顺延。"

    events = [json.loads(item) async for item in orch.execute_stream("年假能顺延吗？", "t1", "u1", kb_id="kb1")]
    assert any(e["event"] == "chunk" for e in events)
    done = next(e for e in events if e["event"] == "done")
    payload = json.loads(done["data"])
    assert payload["final_answer"] == result.answer
    assert payload["has_evidence"] is True


@pytest.mark.asyncio
async def test_stream_no_evidence_fallback_matches_non_stream():
    orch = _orchestrator([])

    result = await orch.execute("年假能顺延吗？", "t1", "u1", kb_id="kb1")
    assert result.answer == NO_EVIDENCE_TEMPLATE

    events = [json.loads(item) async for item in orch.execute_stream("年假能顺延吗？", "t1", "u1", kb_id="kb1")]
    done = next(e for e in events if e["event"] == "done")
    payload = json.loads(done["data"])
    assert payload["final_answer"] == NO_EVIDENCE_TEMPLATE
    assert payload["has_evidence"] is False


@pytest.mark.asyncio
async def test_stream_final_answer_matches_joined_chunks_when_no_fallback():
    """With evidence present, the authoritative terminal answer equals the
    concatenation of the emitted chunks."""
    orch = _orchestrator([{"source": "员工手册.pdf", "section": "4.2", "content": "年假可顺延", "confidence": 0.9}])

    events = [json.loads(item) async for item in orch.execute_stream("年假能顺延吗？", "t1", "u1", kb_id="kb1")]
    joined = "".join(json.loads(e["data"])["text"] for e in events if e["event"] == "chunk")
    done = next(e for e in events if e["event"] == "done")
    payload = json.loads(done["data"])
    assert payload["final_answer"] == joined


@pytest.mark.asyncio
async def test_stream_never_emits_raw_output_that_the_output_guard_replaced():
    orch = _orchestrator([{"source": "员工手册.pdf", "section": "4.2", "content": "年假可顺延", "confidence": 0.9}])
    orch.output_guard = cast(Any, _ReplacingOutputGuard())

    events = [json.loads(item) async for item in orch.execute_stream("年假能顺延吗？", "t1", "u1", kb_id="kb1")]
    visible = "".join(json.loads(e["data"])["text"] for e in events if e["event"] == "chunk")
    done = next(e for e in events if e["event"] == "done")

    assert visible == "安全的最终回答"
    assert json.loads(done["data"])["final_answer"] == visible
