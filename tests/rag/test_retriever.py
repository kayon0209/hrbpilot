"""Retriever tests: three strategies, external-service errors, no mock fallback."""

import pytest

from app.rag.config_loader import RetrievalStrategy
from app.rag.retrieval.retriever import Retriever
from app.rag.retrieval.types import RetrievedChunk


class _FakeEmbedder:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return [[0.5] * 8 for _ in texts]


class _FakeMilvus:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.last = None

    async def search_async(self, vector, tenant_id, kb_id, top_k):
        self.last = (tenant_id, kb_id, top_k)
        if self.error:
            raise self.error
        return self.hits


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.last_params = None

    async def execute(self, stmt, params=None):
        self.last_params = params
        return _FakeResult(self._rows)

    async def close(self):
        pass


def _chunk(cid: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id="d",
        kb_id="k",
        source="f",
        section="s",
        content="c",
        score=score,
    )


async def test_dense_returns_hydrated_chunks(monkeypatch):
    chunk = _Row(id="c1", document_id="d1", kb_id="k1", content="内容", section="第一节")
    fs = _FakeSession(rows=[(chunk, "制度.pdf")])

    async def fake_make(tenant_id):
        return fs

    monkeypatch.setattr("app.rag.retrieval.retriever.make_tenant_session", fake_make)
    r = Retriever(embedder=_FakeEmbedder(), milvus=_FakeMilvus(hits=[("c1", 0.95)]))
    chunks = await r._dense("query", "k1", "tenant-A", 5)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "c1"
    assert chunks[0].source == "制度.pdf"
    assert chunks[0].score == 0.95


async def test_sparse_returns_ranked_chunks(monkeypatch):
    row = _Row(id="c2", document_id="d2", kb_id="k2", content="条款", section="第三章", filename="手册.pdf", rank=7.5)
    fs = _FakeSession(rows=[row])

    async def fake_make(tenant_id):
        return fs

    monkeypatch.setattr("app.rag.retrieval.retriever.make_tenant_session", fake_make)
    r = Retriever()
    chunks = await r._sparse("请假", "k2", "tenant-B", 5)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "c2"
    assert chunks[0].sparse_score == 7.5


async def test_hybrid_fuses_and_dedups(monkeypatch):
    r = Retriever(embedder=_FakeEmbedder(), milvus=_FakeMilvus())

    async def fake_dense(q, kb, t, k):
        return [_chunk("a", 0.9), _chunk("b", 0.8)]

    async def fake_sparse(q, kb, t, k):
        return [_chunk("b", 5.0), _chunk("c", 4.0)]

    monkeypatch.setattr(r, "_dense", fake_dense)
    monkeypatch.setattr(r, "_sparse", fake_sparse)
    chunks = await r._hybrid("q", "k1", "t", 5)
    assert {c.chunk_id for c in chunks} == {"a", "b", "c"}


async def test_retrieve_propagates_embedding_error():
    r = Retriever(embedder=_FakeEmbedder(error=RuntimeError("embedding down")), milvus=_FakeMilvus())
    with pytest.raises(RuntimeError):
        await r.retrieve("q", "k1", RetrievalStrategy.DENSE, 5, False, "t")


async def test_retrieve_propagates_milvus_error():
    r = Retriever(embedder=_FakeEmbedder(), milvus=_FakeMilvus(error=RuntimeError("milvus down")))
    with pytest.raises(RuntimeError):
        await r.retrieve("q", "k1", RetrievalStrategy.DENSE, 5, False, "t")


async def test_dense_empty_hits_returns_empty_not_mock():
    r = Retriever(embedder=_FakeEmbedder(), milvus=_FakeMilvus(hits=[]))
    chunks = await r._dense("q", "k1", "t", 5)
    assert chunks == []


# ── 混合检索的单腿降级（2026-09-11） ──────────────────────────────────────────
# 修复前 `_hybrid` 用 asyncio.gather(...) 且不带 return_exceptions：
# Milvus 一挂，异常会传播并**丢弃已经成功的 sparse 结果**，整条问答链路变成
# ExternalServiceError。修复后两条腿互相独立：坏一条降级到另一条并留下 ERROR 日志，
# 两条都坏才重新抛出（宁可承认失败，也不要在检索栈全挂时返回"没有结果"）。


async def test_hybrid_degrades_to_sparse_when_dense_fails():
    r = Retriever()

    async def failing_dense(q, kb, t, k):
        raise RuntimeError("milvus down")

    async def ok_sparse(q, kb, t, k):
        return [_chunk("s1", 5.0)]

    r._dense = failing_dense
    r._sparse = ok_sparse
    chunks = await r._hybrid("q", "k1", "t", 5)
    assert [c.chunk_id for c in chunks] == ["s1"]


async def test_hybrid_degrades_to_dense_when_sparse_fails():
    r = Retriever()

    async def ok_dense(q, kb, t, k):
        return [_chunk("d1", 0.9)]

    async def failing_sparse(q, kb, t, k):
        raise RuntimeError("postgres fts down")

    r._dense = ok_dense
    r._sparse = failing_sparse
    chunks = await r._hybrid("q", "k1", "t", 5)
    assert [c.chunk_id for c in chunks] == ["d1"]


async def test_hybrid_reraises_when_both_legs_fail():
    """两条腿都坏 → 抛出，而不是用空上下文"作答"。"""
    r = Retriever()

    async def failing(q, kb, t, k):
        raise RuntimeError("both down")

    r._dense = failing
    r._sparse = failing
    with pytest.raises(RuntimeError):
        await r._hybrid("q", "k1", "t", 5)


async def test_hybrid_degradation_is_logged_not_silent(caplog, capsys):
    """降级必须可见：这是"静默降级"与"可观测降级"的分界线。

    两路都收，因为 structlog 的落点取决于 `setup_logging()` 有没有被调用过：
    单跑本文件时未配置 → 直写 stdout（capsys 能收到）；
    全量跑时 `app.main` 会先配置 → 转进 stdlib logging（caplog 才收到）。
    只走一路会在另一种运行方式下假失败。
    """
    import logging

    r = Retriever()

    async def failing_dense(q, kb, t, k):
        raise RuntimeError("milvus down")

    async def ok_sparse(q, kb, t, k):
        return [_chunk("s1", 5.0)]

    r._dense = failing_dense
    r._sparse = ok_sparse
    with caplog.at_level(logging.ERROR):
        await r._hybrid("q", "k1", "t", 5)

    stdlib_events = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.ERROR]
    blob = "\n".join(stdlib_events) + capsys.readouterr().out
    assert "retrieval_leg_failed" in blob
    assert "hybrid_retrieval_degraded_to_single_leg" in blob
