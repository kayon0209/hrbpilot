"""PR-03 stream consistency tests for policy QA.

Locks that the SSE ``done`` event exposes the authoritative terminal answer
(``final_answer``), which may differ from the raw chunk stream when
no-evidence fallback or output guardrails replace the content.
"""

import json
from typing import Any, cast

import pytest

from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.scenarios.policy_qa.postprocessors import NO_EVIDENCE_TEMPLATE
from app.scenarios.policy_qa.schemas import QAResponse


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
