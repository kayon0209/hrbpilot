"""HR Case agent read-tool executors（生产读工具接线）回归。

背景：`agent_loop.run_plan` 的读步骤走全局 `TOOL_EXECUTORS`，而生产没有任何
注册点，于是每个读步骤都静默 `HANDED_OFF`（no executor registered for
search_policy）。本文件锁定修复后的四个行为：

1. 未绑定租户 → TENANT_CONTEXT_MISSING（不猜测租户、不越权检索）；
2. 有租户 + 检索有命中 → 返回真实片段与摘要；
3. 有租户 + 零命中 → 明确回报"未命中"，**不编造**制度依据；
4. 基础设施不可用 → RETRIEVAL_UNAVAILABLE（由 loop 重试一次后交人工）。
"""
from __future__ import annotations

import pytest

from app.scenarios.hr_case_agent import read_executors
from app.scenarios.hr_case_agent.read_context import bind_read_tenant, reset_read_tenant
from app.scenarios.hr_case_agent.tools import ToolError


async def _fake_resolve_kb_id(tenant_id: str, kb_id: str | None) -> str:
    return kb_id or "kb-1"


def _chunk(**overrides) -> dict:
    payload = {
        "chunk_id": "c1",
        "document_id": "d1",
        "kb_id": "kb-1",
        "source": "薪酬福利管理制度.docx",
        "section": "第三章 加班费",
        "content": "加班费按 1.5/2/3 倍计算",
        "score": 1.0,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_search_policy_requires_a_bound_tenant() -> None:
    with pytest.raises(ToolError) as excinfo:
        await read_executors.execute_search_policy({"query": "加班费"})
    assert excinfo.value.code == "TENANT_CONTEXT_MISSING"


@pytest.mark.asyncio
async def test_search_policy_returns_real_hits(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)

    class FakeRetriever:
        async def retrieve(self, **kwargs):
            return [_chunk()]

    monkeypatch.setattr("app.rag.retrieval.retriever.Retriever", lambda *a, **k: FakeRetriever())

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "加班费", "top_k": 3})
    finally:
        reset_read_tenant(token)

    assert "命中 1 条" in result["summary"]
    assert result["kb_id"] == "kb-1"
    assert result["chunks"][0]["source"] == "薪酬福利管理制度.docx"


@pytest.mark.asyncio
async def test_search_policy_reports_empty_instead_of_inventing(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)

    class EmptyRetriever:
        async def retrieve(self, **kwargs):
            return []

    monkeypatch.setattr("app.rag.retrieval.retriever.Retriever", lambda *a, **k: EmptyRetriever())

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "不存在的制度"})
    finally:
        reset_read_tenant(token)

    assert result["chunks"] == []
    assert "未命中" in result["summary"]


@pytest.mark.asyncio
async def test_search_policy_surfaces_infrastructure_failure(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)

    class BrokenRetriever:
        async def retrieve(self, **kwargs):
            raise ConnectionError("milvus unreachable")

    monkeypatch.setattr("app.rag.retrieval.retriever.Retriever", lambda *a, **k: BrokenRetriever())

    token = bind_read_tenant("tenant-1")
    try:
        with pytest.raises(ToolError) as excinfo:
            await read_executors.execute_search_policy({"query": "加班费"})
    finally:
        reset_read_tenant(token)

    assert excinfo.value.code == "RETRIEVAL_UNAVAILABLE"


def test_read_executors_cover_every_read_tool() -> None:
    """读工具集合与执行器注册表必须一一对应，否则又会退化成 HANDED_OFF。"""
    from app.scenarios.hr_case_agent.tools import TOOL_KINDS

    read_tools = {name for name, kind in TOOL_KINDS.items() if kind == "read"}
    assert read_tools == set(read_executors.READ_TOOL_EXECUTORS)
