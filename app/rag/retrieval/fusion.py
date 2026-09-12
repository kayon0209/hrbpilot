"""Reciprocal Rank Fusion (RRF) for hybrid retrieval.

RRF merges two ranked lists (dense + sparse) without normalizing their scores,
which live on different scales (cosine similarity vs ``ts_rank_cd``).

Formula::

    score(chunk) = sum over lists of 1 / (k + rank)

where ``rank`` is the 0-based position of the chunk in that list, and ``k=60``
by default. A chunk appearing in both lists accumulates score from both.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from app.rag.retrieval.types import RetrievedChunk

DEFAULT_K = 60


def dense_confidence(score: float | None) -> float:
    return min(1.0, max(0.0, float(score or 0.0)))


def sparse_confidence(score: float | None) -> float:
    return 1.0 - math.exp(-max(0.0, float(score or 0.0)))


def rrf_fusion(
    dense: list[RetrievedChunk],
    sparse: list[RetrievedChunk],
    k: int = DEFAULT_K,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists via RRF, deduplicating by ``chunk_id``.

    Inputs must already be ranked (list order == rank). The returned chunks
    carry 1-based ``dense_rank``/``sparse_rank`` and the fused ``score``; the
    raw per-list scores are preserved in ``dense_score``/``sparse_score``.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")

    scores: dict[str, float] = {}
    chunks_by_id: dict[str, RetrievedChunk] = {}

    for rank0, chunk in enumerate(dense):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank0)
        chunks_by_id[chunk.chunk_id] = replace(chunk, dense_rank=rank0 + 1, dense_score=chunk.score)

    for rank0, chunk in enumerate(sparse):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank0)
        existing = chunks_by_id.get(chunk.chunk_id)
        if existing is None:
            chunks_by_id[chunk.chunk_id] = replace(chunk, sparse_rank=rank0 + 1, sparse_score=chunk.score)
        else:
            chunks_by_id[chunk.chunk_id] = replace(existing, sparse_rank=rank0 + 1, sparse_score=chunk.score)

    fused = []
    for cid in scores:
        chunk = chunks_by_id[cid]
        dense_value = dense_confidence(chunk.dense_score)
        sparse_value = sparse_confidence(chunk.sparse_score)
        confidence = 1.0 - (1.0 - dense_value) * (1.0 - sparse_value)
        fused.append(replace(chunk, score=scores[cid], confidence=confidence))
    fused.sort(key=lambda c: c.score, reverse=True)

    if top_k is not None:
        fused = fused[:top_k]
    return fused


def fuse_query_variants(
    primary: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    top_k: int | None = None,
    k: int = DEFAULT_K,
) -> list[dict[str, Any]]:
    """Fuse two retrieval runs that differ only in the query text (§6.1).

    A rewrite can silently drop the user's intent. Replacing the original query
    with it then makes retrieval WORSE than not rewriting at all. Running both
    queries and fusing with RRF keeps the original's recall while adding
    whatever the rewrite found.

    Differences from ``rrf_fusion``:
      * operates on the unified dicts returned by ``Retriever.retrieve``;
      * does NOT stamp dense_rank/sparse_rank — both lists come from the same
        strategy, so those fields would be meaningless here;
      * leaves ``score`` untouched and ranks by a new ``fused_score``, so
        downstream code that thresholds on the native score keeps working.

    Deduplicated by chunk_id; a chunk found by both paths accumulates score
    from both.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")

    scores: dict[str, float] = {}
    merged: dict[str, dict[str, Any]] = {}

    def _absorb(ranked: list[dict[str, Any]]) -> None:
        for rank0, chunk in enumerate(ranked):
            # Key on chunk_id, falling back to content: a chunk without an id
            # is still evidence and must be fused, not silently dropped.
            cid = str(chunk.get("chunk_id") or f"__content__:{chunk.get('content', '')}")
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank0)
            existing = merged.get(cid)
            if existing is None:
                merged[cid] = dict(chunk)
            elif existing.get("page_number") is None and chunk.get("page_number") is not None:
                # keep the more informative record when one path knows the page
                existing["page_number"] = chunk["page_number"]

    _absorb(primary)
    _absorb(variant)

    fused: list[dict[str, Any]] = []
    for cid, score in scores.items():
        item = dict(merged[cid])
        item["fused_score"] = score
        fused.append(item)
    fused.sort(key=lambda item: item["fused_score"], reverse=True)
    return fused[:top_k] if top_k is not None else fused
