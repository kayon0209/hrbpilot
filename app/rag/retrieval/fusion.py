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
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.data.models.knowledge_base import SourceAuthority
from app.rag.retrieval.types import RetrievedChunk

DEFAULT_K = 60

#: 分数**完全相等**时的次级排序：更权威的来源排前（数值越大越靠前）。
#: 刻意不把 ``company_policy`` 排在 ``national_law`` 之上/之下 —— 两者都是
#: 可作依据的来源，平局场景里谁先都有理，不为极少见的组合引入更多语义。
_AUTHORITY_TIE_RANK: dict[str, int] = {
    SourceAuthority.NATIONAL_LAW.value: 2,
    SourceAuthority.COMPANY_POLICY.value: 2,
    SourceAuthority.VENDOR_TEMPLATE.value: 1,
    SourceAuthority.REFERENCE.value: 1,
    SourceAuthority.UNKNOWN.value: 0,
}


def apply_authority_tie_break(
    items: list[Any],
    *,
    score_of: Callable[[Any], float],
    authority_of: Callable[[Any], str],
) -> list[Any]:
    """仅对**分数完全相等**的相邻项，按来源级别从高到低稳定重排。

    为什么需要它
    ------------
    ``fused.sort(key=score)`` 是稳定排序：分数持平的先后由**插入顺序**决定 ——
    也就是"哪条检索腿先产出谁"，与相关性和来源权威性都无关。真实事故里，
    《职工带薪年休假条例》与一份酒店技能考核表的融合分**完全相同**（0.0167），
    名次由这种偶然顺序决定。

    为什么是"完全相等"而不是"接近"
    ------------------------------
    RRF 的不同取值之间相差 1e-2 量级；把阈值放宽到"接近"，来源级别就开始
    干预**有分差**的排序 —— 那是相关性判断，不该被覆盖。所以这里用精确相等，
    只裁决真正的平局。

    已知边界：``_rerank`` 在本函数之后重排，rerank 分数的平局不再经过本裁决
    （当前配置 rerank 关闭；开启时需要把同样的裁决接到 rerank 之后）。
    """
    ordered = list(items)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and score_of(ordered[end]) == score_of(ordered[start]):
            end += 1
        if end - start > 1:
            group = sorted(
                ordered[start:end],
                key=lambda item: -_AUTHORITY_TIE_RANK.get(authority_of(item), 0),
            )
            ordered[start:end] = group
        start = end
    return ordered


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
    # 来源级别须在融合**前**回填（见 Retriever._hybrid_with_diagnostics），
    # 否则这里人人都是 unknown，裁决形同虚设。
    fused = apply_authority_tie_break(
        fused, score_of=lambda c: c.score, authority_of=lambda c: c.authority
    )

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
    # 与 rrf_fusion 同一裁决：dict 形态的命中带有 retrieve() 回填的 authority。
    # 若这里不接，policy_qa 的 query 变体路径就会把平局裁决静默丢掉 ——
    # "约定只在部分地方落实"正是本项目反复出现的缺陷形状。
    fused = apply_authority_tie_break(
        fused,
        score_of=lambda item: item["fused_score"],
        authority_of=lambda item: str(item.get("authority") or "unknown"),
    )
    return fused[:top_k] if top_k is not None else fused
