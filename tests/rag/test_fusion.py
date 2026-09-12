"""RRF fusion unit tests: ranking, dedup, top-k, empty recall."""

import pytest

from app.rag.retrieval.fusion import fuse_query_variants, rrf_fusion
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


# --- dual-path (original + rewritten query) fusion, §6.1 ---


def _hit(cid: str, score: float = 0.9, page: int | None = None) -> dict:
    return {"chunk_id": cid, "content": f"content-{cid}", "score": score, "page_number": page}


def test_fuse_query_variants_unions_both_paths():
    """A rewrite must ADD recall, never replace the original query's recall."""
    original = [_hit("a"), _hit("b")]
    rewritten = [_hit("c")]

    fused = fuse_query_variants(original, rewritten)

    assert {item["chunk_id"] for item in fused} == {"a", "b", "c"}


def test_fuse_query_variants_dedups_and_boosts_chunks_found_by_both():
    original = [_hit("a"), _hit("b")]
    rewritten = [_hit("a"), _hit("c")]

    fused = fuse_query_variants(original, rewritten)
    ids = [item["chunk_id"] for item in fused]

    assert ids.count("a") == 1  # deduplicated
    assert ids[0] == "a"  # found by both -> highest fused score


def test_fuse_query_variants_preserves_native_score():
    """Downstream code thresholds on the native score, so it must survive."""
    fused = fuse_query_variants([_hit("a", score=0.42)], [_hit("b")])
    a = next(item for item in fused if item["chunk_id"] == "a")

    assert a["score"] == 0.42
    assert "fused_score" in a


def test_fuse_query_variants_respects_top_k():
    fused = fuse_query_variants([_hit("a"), _hit("b")], [_hit("c")], top_k=2)
    assert len(fused) == 2


def test_fuse_query_variants_keeps_page_number_from_either_path():
    """One path may know the page while the other does not; keep the fact."""
    fused = fuse_query_variants([_hit("a", page=None)], [_hit("a", page=7)])
    a = next(item for item in fused if item["chunk_id"] == "a")

    assert a["page_number"] == 7


def test_fuse_query_variants_handles_empty_paths():
    assert fuse_query_variants([], []) == []
    assert [item["chunk_id"] for item in fuse_query_variants([_hit("a")], [])] == ["a"]


def test_fuse_query_variants_keeps_chunks_without_chunk_id():
    """Regression: chunks without a chunk_id are still evidence. Keying only on
    chunk_id silently dropped them and left the answer with no context at all."""
    no_id = [{"content": "年假可顺延", "source": "员工手册.pdf"}]

    fused = fuse_query_variants(no_id, [])

    assert len(fused) == 1
    assert fused[0]["content"] == "年假可顺延"


def test_fuse_query_variants_dedups_identical_content_without_id():
    same = [{"content": "年假可顺延"}, {"content": "年假可顺延"}]

    assert len(fuse_query_variants(same, [])) == 1


def test_fuse_query_variants_rejects_non_positive_k():
    with pytest.raises(ValueError, match="k must be positive"):
        fuse_query_variants([_hit("a")], [], k=0)
