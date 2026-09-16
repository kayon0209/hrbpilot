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

from app.rag.retrieval.retriever import RetrievalDiagnostics
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
        # 默认给"本单位制度"：这是正常命中应有的样子。
        # 非权威来源单独构造（见 test_search_policy_flags_hits_without_authoritative_source），
        # 否则默认值会把"没有依据"这条路径变成测不到的。
        "authority": "company_policy",
    }
    payload.update(overrides)
    return payload


class _FakeRetriever:
    """只实现 `retrieve_with_diagnostics`（生产在 2026-09-16 后调的就是它）。

    刻意**不**提供 `retrieve`：万一以后有人把 `execute_search_policy` 改回旧入口，
    这里会直接 AttributeError 炸掉，而不是让"降级诊断"悄悄丢失却测试全绿。
    """

    def __init__(self, chunks: list[dict] | None = None, diags: RetrievalDiagnostics | None = None):
        self._chunks = [_chunk()] if chunks is None else chunks
        self._diags = diags or RetrievalDiagnostics(strategy="hybrid")

    async def retrieve_with_diagnostics(self, **kwargs):
        return self._chunks, self._diags


class _BrokenRetriever:
    async def retrieve_with_diagnostics(self, **kwargs):
        raise ConnectionError("milvus unreachable")


@pytest.mark.asyncio
async def test_search_policy_requires_a_bound_tenant() -> None:
    with pytest.raises(ToolError) as excinfo:
        await read_executors.execute_search_policy({"query": "加班费"})
    assert excinfo.value.code == "TENANT_CONTEXT_MISSING"


@pytest.mark.asyncio
async def test_search_policy_returns_real_hits(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever", lambda *a, **k: _FakeRetriever()
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "加班费", "top_k": 3})
    finally:
        reset_read_tenant(token)

    assert "命中 1 条" in result["summary"]
    assert result["kb_id"] == "kb-1"
    assert result["chunks"][0]["source"] == "薪酬福利管理制度.docx"
    # 两条腿都正常 → 响应里**不该**出现降级字段（字段本身就是信号）。
    assert "retrieval_degraded" not in result
    assert "retrieval_note" not in result


@pytest.mark.asyncio
async def test_search_policy_reports_empty_instead_of_inventing(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever", lambda *a, **k: _FakeRetriever(chunks=[])
    )

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
    monkeypatch.setattr("app.rag.retrieval.retriever.Retriever", lambda *a, **k: _BrokenRetriever())

    token = bind_read_tenant("tenant-1")
    try:
        with pytest.raises(ToolError) as excinfo:
            await read_executors.execute_search_policy({"query": "加班费"})
    finally:
        reset_read_tenant(token)

    assert excinfo.value.code == "RETRIEVAL_UNAVAILABLE"


# ── 降级必须到达调用方（2026-09-16） ──────────────────────────────────────────
# 事故：dense 腿因 embedding 401 静默消失后，本工具照回 outcome: FOUND 与一段
# 自信摘要，把字面命中"年""假"的酒店考核表当成《职工带薪年休假条例》答了出去。
# 下面两条锁住"降级要么出现在结构字段里、要么出现在摘要里"——两者都缺就是事故重演。


@pytest.mark.asyncio
async def test_search_policy_marks_degraded_retrieval(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    degraded = RetrievalDiagnostics(
        strategy="hybrid",
        degraded_legs=("dense",),
        notes=("语义（向量）检索本次不可用，结果排序已退化为仅依赖剩余的一种检索方式。",),
    )
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(diags=degraded),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "年假"})
    finally:
        reset_read_tenant(token)

    assert result["retrieval_degraded"] == ["dense"]
    assert "语义（向量）" in result["retrieval_note"]
    assert "【注意】" in result["summary"]
    # 降级不等于失败：片段仍然要返回，并保留 FOUND 的证据字段。
    assert result["chunks"]


@pytest.mark.asyncio
async def test_search_policy_marks_degradation_even_when_nothing_matched(monkeypatch) -> None:
    """零命中 + 降级：必须能区分"确实没有"和"只用了关键词也没找到"。"""
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    degraded = RetrievalDiagnostics(
        strategy="hybrid", degraded_legs=("dense",), notes=("语义（向量）检索本次不可用。",)
    )
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(chunks=[], diags=degraded),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "年假"})
    finally:
        reset_read_tenant(token)

    assert result["chunks"] == []
    assert result["retrieval_degraded"] == ["dense"]


def test_read_executors_cover_every_read_tool() -> None:
    """读工具集合与执行器注册表必须一一对应，否则又会退化成 HANDED_OFF。"""
    from app.scenarios.hr_case_agent.tools import TOOL_KINDS

    read_tools = {name for name, kind in TOOL_KINDS.items() if kind == "read"}
    assert read_tools == set(read_executors.READ_TOOL_EXECUTORS)


# ── 来源性质必须到达调用方（2026-09-16）─────────────────────────────────────
# 真实事故的另一半：dense 腿恢复之后，`search_policy("公司的年假是怎么规定的？")`
# 的 top-1 已经是对的（国务院令第514号），**但 #2 仍是一份酒店工程部的技能考核表**
# —— 它格式合法、解析无误，只是跟制度毫无关系。排序修好了，语料没修好。
#
# 知识库里同时存在：国家法规、第三方模板（hrtools/用友/三茅，其中手册模板的年假
# 条款写的是"×天"占位符）、以及无关材料。三者此前在响应里毫无区别，调用方只能
# 看文档名猜。这一组锁住"命中的性质"与"有没有真依据"两件事都写进响应。


@pytest.mark.asyncio
async def test_chunk_view_declares_the_source_type(monkeypatch) -> None:
    """每条命中都要说明它是什么性质的材料，两档 detail 都要有。"""
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[
                _chunk(authority="company_policy"),
                _chunk(chunk_id="c2", source="hrtools模板.docx", authority="vendor_template"),
            ]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        concise = await read_executors.execute_search_policy(
            {"query": "年假", "top_k": 3, "detail": "concise"}
        )
        detailed = await read_executors.execute_search_policy(
            {"query": "年假", "top_k": 3, "detail": "detailed"}
        )
    finally:
        reset_read_tenant(token)

    assert concise["chunks"][0]["source_type"] == "本单位制度"
    assert concise["chunks"][1]["source_type"] == "第三方模板"
    assert detailed["chunks"][0]["source_type"] == "本单位制度"


@pytest.mark.asyncio
async def test_search_policy_flags_hits_without_authoritative_source(monkeypatch) -> None:
    """命中的全是第三方模板/无关材料 → 必须显式说"没有可依据的制度"。

    这是本组测试的主要理由。`outcome` 仍是 FOUND、摘要依旧"命中 N 条"，
    调用方完全可能照旧作答 —— 除非有一条明确的"这些不能当依据"。
    """
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[
                _chunk(source="11_酒店工程部员工转正、晋级技能考核表.docx", authority="reference"),
                _chunk(chunk_id="c2", source="hrtools员工手册模板v3.txt", authority="vendor_template"),
            ]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "公司的年假是怎么规定的？"})
    finally:
        reset_read_tenant(token)

    assert result["authoritative_source"] is False
    assert "没有任何一条来自国家法规或本单位制度" in result["authority_note"]
    # 摘要里也要说一次：它是最可能被直接读到的字段。
    assert result["summary"].startswith("【注意】")
    assert "不能作为制度依据" in result["summary"]


@pytest.mark.asyncio
async def test_search_policy_stays_quiet_when_a_real_source_is_present(monkeypatch) -> None:
    """有真依据时**不加**这两个字段 —— 否则标记会爬满正常响应并被忽略。"""
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[
                _chunk(source="09_国务院_职工带薪年休假条例.txt", authority="national_law"),
                _chunk(chunk_id="c2", source="酒店考核表.docx", authority="reference"),
            ]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "年假"})
    finally:
        reset_read_tenant(token)

    assert "authoritative_source" not in result
    assert "authority_note" not in result
    assert not result["summary"].startswith("【注意】")


# ── authoritative_only 过滤（2026-09-16，遗留项②）───────────────────────────
# 给"必须给出处"的问题（公司是怎么规定的）一个只要权威来源的开关。三条契约：
# 默认零变化 / 混合命中留权威 / 全被滤掉时必须说清"命中过但被滤掉"——
# 最后一条最要紧：它与"真的没查到"是两种不同的无依据，混起来会让调用方
# 去怀疑检索通道，而真正缺的是语料。


@pytest.mark.asyncio
async def test_authoritative_only_defaults_to_off(monkeypatch) -> None:
    """不带参数 → 行为一字不变（模板命中照常返回）。"""
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[_chunk(source="hrtools模板.docx", authority="vendor_template")]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy({"query": "年假"})
    finally:
        reset_read_tenant(token)

    assert len(result["chunks"]) == 1
    # 模板命中会触发"无权威来源"警示（那是命中性质决定的，与开关无关）；
    # 这条锁的是**开关本身**：未开启时不做过滤，片段仍在、也没有"被滤掉"话术。
    assert "均为第三方模板" not in result["summary"]


@pytest.mark.asyncio
async def test_authoritative_only_keeps_the_real_sources(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[
                _chunk(source="酒店考核表.docx", authority="reference"),
                _chunk(chunk_id="c2", source="09_国务院条例.txt", authority="national_law"),
                _chunk(chunk_id="c3", source="hrtools模板.txt", authority="vendor_template"),
            ]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy(
            {"query": "年假", "authoritative_only": True}
        )
    finally:
        reset_read_tenant(token)

    assert [c["source"] for c in result["chunks"]] == ["09_国务院条例.txt"]
    # 过滤后剩下的全部权威 → 不再有"无依据"警示。
    assert "authoritative_source" not in result


@pytest.mark.asyncio
async def test_authoritative_only_reports_filtered_out_instead_of_empty(monkeypatch) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever",
        lambda *a, **k: _FakeRetriever(
            chunks=[_chunk(source="hrtools模板.docx", authority="vendor_template")]
        ),
    )

    token = bind_read_tenant("tenant-1")
    try:
        result = await read_executors.execute_search_policy(
            {"query": "年假", "authoritative_only": True}
        )
    finally:
        reset_read_tenant(token)

    assert result["chunks"] == []
    assert result["authoritative_source"] is False
    assert "均为第三方模板" in result["summary"], "摘要必须说清是'被过滤'而不是'没查到'"
