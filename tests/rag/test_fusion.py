"""RRF fusion unit tests: ranking, dedup, top-k, empty recall."""

import pytest

from app.rag.retrieval.fusion import rrf_fusion
from app.rag.retrieval.types import RetrievedChunk


def _chunk(cid: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id="d1",
        kb_id="k1",
        source="f.txt",
        section="s",
        content="c",
        score=score,
    )


def test_rrf_basic_ranking():
    dense = [_chunk("a"), _chunk("b")]
    sparse = [_chunk("c"), _chunk("d")]
    fused = rrf_fusion(dense, sparse, k=60)
    assert len(fused) == 4
    # top two are the rank-1 items (a and c), each 1/60
    assert {fused[0].chunk_id, fused[1].chunk_id} == {"a", "c"}
    assert fused[0].score == pytest.approx(1 / 60)


def test_rrf_dedup_sums_scores():
    dense = [_chunk("a"), _chunk("b")]
    sparse = [_chunk("a"), _chunk("c")]
    fused = rrf_fusion(dense, sparse, k=60)
    assert len(fused) == 3  # "a" deduplicated
    a = next(c for c in fused if c.chunk_id == "a")
    assert a.score == pytest.approx(2 / 60)  # 1/60 (dense) + 1/60 (sparse)
    assert a.dense_rank == 1
    assert a.sparse_rank == 1


def test_rrf_top_k_truncates():
    dense = [_chunk(f"d{i}") for i in range(10)]
    sparse = [_chunk(f"s{i}") for i in range(10)]
    fused = rrf_fusion(dense, sparse, k=60, top_k=5)
    assert len(fused) == 5


def test_rrf_empty_recall():
    assert rrf_fusion([], [], k=60) == []
    dense = [_chunk("a")]
    assert [c.chunk_id for c in rrf_fusion(dense, [], k=60)] == ["a"]
    sparse = [_chunk("z")]
    assert [c.chunk_id for c in rrf_fusion([], sparse, k=60)] == ["z"]


def test_rrf_invalid_k_raises():
    with pytest.raises(ValueError):
        rrf_fusion([], [], k=0)


def test_rrf_preserves_native_scores():
    dense = [_chunk("a", score=0.95)]
    sparse = [_chunk("b", score=12.3)]
    fused = rrf_fusion(dense, sparse, k=60)
    a = next(c for c in fused if c.chunk_id == "a")
    b = next(c for c in fused if c.chunk_id == "b")
    assert a.dense_score == 0.95
    assert b.sparse_score == 12.3
    assert 0.0 <= a.confidence <= 1.0
    assert 0.0 <= b.confidence <= 1.0
