"""RRF 平局的来源级别裁决（``fusion.apply_authority_tie_break``）。

原事故的机制之一：`search_policy("公司的年假是怎么规定的？")` 里，国务院条例与
一份酒店技能考核表的融合分**完全相同**（0.016667 = 1/(60+0)），而
``list.sort`` 是稳定排序 —— 平局的先后由"哪条腿先产出谁"这种偶然因素决定。
本组测试锁住：只有真正持平的分数才被来源级别裁决；有任何分差，相关性优先。
"""

from __future__ import annotations

from app.rag.retrieval.fusion import apply_authority_tie_break, rrf_fusion
from app.rag.retrieval.types import RetrievedChunk


def _chunk(chunk_id: str, score: float, authority: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        kb_id="kb-1",
        source=chunk_id,
        section="",
        content="正文",
        score=score,
        authority=authority,
    )


def _ids(chunks) -> list[str]:
    return [c.chunk_id for c in chunks]


def test_an_exact_tie_goes_to_the_more_authoritative_source():
    tied = [_chunk("酒店考核表", 0.5, "vendor_template"), _chunk("国务院条例", 0.5, "national_law")]
    out = apply_authority_tie_break(tied, score_of=lambda c: c.score, authority_of=lambda c: c.authority)
    assert _ids(out) == ["国务院条例", "酒店考核表"]


def test_any_score_difference_keeps_relevance_in_charge():
    """分差哪怕不大，也不能被来源级别覆盖 —— 这是相关性排序的边界。"""
    ranked = [_chunk("模板", 0.9, "vendor_template"), _chunk("条例", 0.1, "national_law")]
    out = apply_authority_tie_break(ranked, score_of=lambda c: c.score, authority_of=lambda c: c.authority)
    assert _ids(out) == ["模板", "条例"]


def test_ties_within_the_same_authority_keep_their_original_order():
    """同级平局不重排（排序必须稳定，否则结果不可复现）。"""
    tied = [_chunk("甲", 0.5, "reference"), _chunk("乙", 0.5, "reference")]
    out = apply_authority_tie_break(tied, score_of=lambda c: c.score, authority_of=lambda c: c.authority)
    assert _ids(out) == ["甲", "乙"]


def test_rrf_fusion_breaks_the_accident_tie():
    """复刻原事故的分数形态：两腿各命中一条，融合分同为 1/(60+0)=0.016667。

    修复前：稳定排序按插入顺序（dense 腿先产出谁谁在前），酒店考核表坐上第一。
    修复后：同级裁决让国家法规排前。
    """
    dense = [_chunk("酒店考核表", 0.9, "vendor_template")]
    sparse = [_chunk("国务院条例", 26.8, "national_law")]

    fused = rrf_fusion(dense, sparse, k=60, top_k=5)

    assert _ids(fused)[0] == "国务院条例"
    assert fused[0].score == fused[1].score, "这条测试只在两分确实持平时才成立"


def test_fuse_query_variants_also_breaks_ties():
    """policy_qa 的 query 变体路径走 dict 形态 —— 不接裁决就会静默丢掉它。"""
    from app.rag.retrieval.fusion import fuse_query_variants

    primary = [dict(_chunk("模板", 0.5, "vendor_template").to_dict(), fused_score=0.02)]
    variant = [dict(_chunk("条例", 0.5, "national_law").to_dict(), fused_score=0.02)]

    fused = fuse_query_variants(primary, variant, top_k=5, k=60)

    assert _ids([type("X", (), {"chunk_id": i["chunk_id"]}) for i in fused]) == ["条例", "模板"]
