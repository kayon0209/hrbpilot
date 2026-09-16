"""HRBP AI Workbench — hybrid retrieval service.

Real implementation:
  - dense:  query embedding -> Milvus cosine search (tenant_id + kb_id filter)
  - sparse: jieba tokenize -> PostgreSQL FTS (plainto_tsquery / ts_rank_cd)
  - hybrid: dense + sparse in parallel -> RRF fusion

There is NO mock fallback: if an external service (Milvus / PostgreSQL /
embedding) is unavailable, an infrastructure error propagates to the caller
rather than silently returning fabricated results.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text

from app.config.settings import settings
from app.data.database import make_tenant_session
from app.data.models.knowledge_base import Document, DocumentChunk, SourceAuthority
from app.rag.config_loader import RetrievalStrategy
from app.rag.embedding import EmbeddingClient, get_embedder
from app.rag.retrieval.fusion import (
    apply_authority_tie_break,
    dense_confidence,
    rrf_fusion,
    sparse_confidence,
)
from app.rag.retrieval.tokenizer import tokenize_for_query
from app.rag.retrieval.types import RetrievedChunk
from app.rag.storage.milvus import MilvusStore
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 检索腿 → 给用户看的名字。降级提示会写进工具响应，所以不能出现
#: "dense"/"sparse" 这类只有实现者才懂的词。
_LEG_LABEL: dict[str, str] = {"dense": "语义（向量）", "sparse": "关键词"}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity that degrades to 0.0 for empty/zero vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = float(sum(a * b for a, b in zip(left, right, strict=True)))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass(frozen=True)
class RetrievalDiagnostics:
    """一次检索的**完整性**回报：排序有没有少了一条腿。

    与 ``RetrievedChunk`` 分开、而不是塞进每个 chunk 的 dict 里，是因为降级是
    **整次检索的属性**，不是某一条片段的属性 —— 复制到每条片段上会让"字段存在"
    不再等于"这条片段有问题"，调用方反而更难判断。

    ``degraded_legs`` 里的取值是腿的名字（``"dense"`` / ``"sparse"``）：
    空元组 = 两条腿都参与了融合（或调用方显式只要了一条腿）。
    """

    strategy: str
    degraded_legs: tuple[str, ...] = ()
    #: 人话版的降级原因，直接给调用方/用户看，不暴露异常类型与堆栈。
    notes: tuple[str, ...] = ()

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_legs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "degraded_legs": list(self.degraded_legs),
            "notes": list(self.notes),
        }

    def prompt_directive(self) -> str:
        """给**模型**的可执行指令；未降级时返回空串。

        为什么与 ``notes`` 分开写
        ------------------------
        ``notes`` 是给**用户**看的说明（"为什么排序可能不准"），要的是人能理解；
        本条是给**模型**的指令（"因此你必须怎么做"），要的是可执行。把两者合并会得到
        一个既不像人话、又不构成明确指令的中间态 —— 而"透出字段但没人告诉模型怎么用"
        正是这次事故的残留：字段到位了，模型照旧会自信地把一个片段当成定论。
        """
        if not self.is_degraded:
            return ""
        labels = "、".join(_LEG_LABEL.get(leg, leg) for leg in self.degraded_legs)
        return (
            f"【检索完整性警告】本次检索的{labels}通道不可用，下面这些制度片段的"
            "相关度排序不可靠：排在前面可能只是字面命中，而不是语义上真的适用。"
            "因此你必须：\n"
            "1. 在「不确定项」中明确指出本次排序可能不准，并提示用户核对原文出处；\n"
            "2. 不得仅因为某片段排在第一位就断言它是适用条款 —— 必要时把多个候选条款"
            "分别列出并说明差异；\n"
            "3. 若无法从片段确认结论，按“未在现有制度中找到可靠依据”处理，"
            "不要用常识补充。"
        )


class Retriever:
    """Retrieve document chunks via dense / sparse / hybrid strategies."""

    def __init__(
        self,
        embedder: EmbeddingClient | None = None,
        milvus: MilvusStore | None = None,
    ) -> None:
        self._embedder = embedder
        self._milvus = milvus

    def _get_embedder(self) -> EmbeddingClient:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def _get_milvus(self) -> MilvusStore:
        if self._milvus is None:
            self._milvus = MilvusStore()
        return self._milvus

    async def retrieve(
        self,
        query: str,
        kb_id: str,
        strategy: RetrievalStrategy = RetrievalStrategy.HYBRID,
        top_k: int = 5,
        rerank: bool = False,
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Retrieve top_k chunks for the query using the given strategy.

        Returns a list of unified dicts (chunk_id, document_id, kb_id, source,
        section, content, score, dense_rank, sparse_rank, dense_score,
        sparse_score).

        只需要片段的调用方用这个入口；需要知道"本次排序是不是只用了单条腿"
        的调用方用 ``retrieve_with_diagnostics``（见 ``RetrievalDiagnostics``）。
        """
        chunks, _ = await self.retrieve_with_diagnostics(
            query=query,
            kb_id=kb_id,
            strategy=strategy,
            top_k=top_k,
            rerank=rerank,
            tenant_id=tenant_id,
        )
        return chunks

    async def retrieve_with_diagnostics(
        self,
        query: str,
        kb_id: str,
        strategy: RetrievalStrategy = RetrievalStrategy.HYBRID,
        top_k: int = 5,
        rerank: bool = False,
        tenant_id: str = "default",
    ) -> tuple[list[dict[str, Any]], RetrievalDiagnostics]:
        """与 ``retrieve`` 同一次检索，额外回报本次排序的完整性与降级原因。

        为什么需要它
        ------------
        ``_hybrid`` 会把坏掉的腿降级成单腿继续作答（dense 与 sparse 是独立的两条腿，
        一条挂了不该拖垮另一条 —— 这个设计本身是对的）。但降级改变了**排序的依据**：
        只剩 sparse 时排序退化成纯关键词匹配，字面命中高频词的文档会压过语义上真正
        相关的那一份。此时调用方拿到的仍然是 ``outcome: FOUND`` 和一段自信的摘要，
        **无从分辨"语义+关键词一致同意"与"只剩关键词在猜"**。

        真实事故：`search_policy("公司的年假是怎么规定的？")` 在 dense 腿因 embedding
        401 而静默消失时，top-1 从《职工带薪年休假条例》变成了一份酒店工程部的技能
        考核表（字面命中"年""假"）。日志和 ``/api/ready`` 都报了，但**没有一条到达
        调用方**。本方法把那条信息补上。

        单腿策略（DENSE / SPARSE）是调用方**显式**要求的，不是降级 ——
        ``degraded_legs`` 为空。只有 HYBRID 少了一条腿才算降级。
        """
        if isinstance(strategy, str):
            strategy = RetrievalStrategy(strategy)

        logger.info(
            "retrieval_requested",
            query=query[:50],
            kb_id=kb_id,
            strategy=strategy.value,
            top_k=top_k,
            rerank=rerank,
            tenant_id=tenant_id,
        )

        diags = RetrievalDiagnostics(strategy=strategy.value)
        if strategy is RetrievalStrategy.DENSE:
            chunks = await self._dense(query, kb_id, tenant_id, top_k)
            chunks = await self._attach_authority(chunks, tenant_id, kb_id=kb_id)
            chunks = apply_authority_tie_break(
                chunks, score_of=lambda c: c.score, authority_of=lambda c: c.authority
            )
        elif strategy is RetrievalStrategy.SPARSE:
            chunks = await self._sparse(query, kb_id, tenant_id, top_k)
            chunks = await self._attach_authority(chunks, tenant_id, kb_id=kb_id)
            chunks = apply_authority_tie_break(
                chunks, score_of=lambda c: c.score, authority_of=lambda c: c.authority
            )
        else:
            # hybrid 路径的回填与平局裁决都在 _hybrid_with_diagnostics 内部完成
            #（裁决发生在 rrf_fusion 排序时，融合后再回填就晚了）。
            chunks, diags = await self._hybrid_with_diagnostics(query, kb_id, tenant_id, top_k)

        if rerank:
            chunks = await self._rerank(query, chunks, top_k)

        logger.info("retrieval_completed", kb_id=kb_id, strategy=strategy.value, count=len(chunks))
        return [c.to_dict() for c in chunks], diags

    async def _attach_authority(
        self,
        chunks: list[RetrievedChunk],
        tenant_id: str,
        kb_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """把每个片段的来源级别（``SourceAuthority``）从所属文档回填进来。

        为什么是"融合后的统一步骤"，而不是在每条腿里各自 select
        --------------------------------------------------------
        dense 腿读 Milvus，它的 payload 要带上新字段就得**重新索引整个语料**；
        sparse 腿读 PostgreSQL 的 ``document_chunks``。若各写一遍，将来加第三条腿
        还得再记一次 —— 而"约定只在部分地方落实"正是本项目反复出现的缺陷形状。
        统一在融合之后查一次 ``documents``，两条腿（以及任何未来的腿）自动都有，
        且不需要重新嵌入。

        代价是每次检索多一条小查询（按 document_id 批量取，通常 1-3 行）。
        相比重新索引语料，这个代价可以接受。

        查不到的行落到 ``unknown``：让"没声明过"保持可见，不要伪装成已知级别。
        """
        document_ids = {chunk.document_id for chunk in chunks if chunk.document_id}
        if not document_ids:
            return chunks

        stmt = select(Document.id, Document.authority).where(
            Document.tenant_id == tenant_id,
            Document.id.in_(document_ids),
        )
        if kb_id is not None:
            stmt = stmt.where(Document.kb_id == kb_id)

        session = await make_tenant_session(tenant_id)
        try:
            rows = (await session.execute(stmt)).all()
        finally:
            await session.close()

        by_document = {document_id: authority for document_id, authority in rows}
        for chunk in chunks:
            chunk.authority = by_document.get(chunk.document_id, SourceAuthority.UNKNOWN.value)
        return chunks

    # --- strategies ---

    async def _dense(self, query: str, kb_id: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        vector = await self._embed_query(query)
        hits = await self._get_milvus().search_async(vector, tenant_id, kb_id, settings.dense_top_k)
        if not hits:
            return []
        return (await self._hydrate(list(hits), kb_id, tenant_id, dense=True))[:top_k]

    async def _sparse(self, query: str, kb_id: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        tokenized = tokenize_for_query(query)
        if not tokenized:
            return []

        # ``plainto_tsquery`` joins every query term with AND.  Natural-language
        # questions invariably include qualifiers that are absent from the
        # source (for example, "多久" in "请假要提前多久申请"), which would turn
        # an otherwise relevant Chinese document into a false negative.  Use
        # the same jieba terms but OR them for recall, then let ts_rank_cd and
        # RRF rank the results.  Terms are parameterized and are only joined
        # with the PostgreSQL tsquery OR operator.
        tsquery = " | ".join(tokenized.split())

        sql = text(
            """
            SELECT c.id, c.document_id, c.kb_id, c.content, c.section, d.filename,
                   c.page_number,
                   ts_rank_cd(c.search_vector, to_tsquery('simple', :q)) AS rank
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.tenant_id = :tenant_id
              AND c.kb_id = :kb_id
              AND c.search_vector @@ to_tsquery('simple', :q)
            ORDER BY rank DESC
            LIMIT :limit
            """
        )

        session = await make_tenant_session(tenant_id)
        try:
            result = await session.execute(
                sql,
                {"q": tsquery, "tenant_id": tenant_id, "kb_id": kb_id, "limit": settings.sparse_top_k},
            )
            rows = result.fetchall()
        finally:
            await session.close()

        chunks: list[RetrievedChunk] = []
        for row in rows:
            chunks.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    kb_id=row.kb_id,
                    source=row.filename,
                    section=row.section or "",
                    content=row.content,
                    page_number=row.page_number,
                    score=float(row.rank or 0.0),
                    confidence=sparse_confidence(float(row.rank or 0.0)),
                    sparse_score=float(row.rank or 0.0),
                )
            )
        return chunks[:top_k]

    async def _hybrid(self, query: str, kb_id: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        """RRF 融合后的片段列表（不含诊断）。

        保留这个签名是刻意的：它是既有测试与集成的锚点，诊断信息通过
        ``_hybrid_with_diagnostics`` / ``retrieve_with_diagnostics`` 暴露，
        不改动这里就等于不改动任何既有调用方的行为。
        """
        chunks, _ = await self._hybrid_with_diagnostics(query, kb_id, tenant_id, top_k)
        return chunks

    async def _hybrid_with_diagnostics(
        self, query: str, kb_id: str, tenant_id: str, top_k: int
    ) -> tuple[list[RetrievedChunk], RetrievalDiagnostics]:
        """Dense + sparse fused with RRF, plus which legs actually contributed.

        Dense (Milvus) and sparse (PostgreSQL FTS) are **independent** legs, so
        one failing must not cancel the other.  ``asyncio.gather`` without
        ``return_exceptions`` propagates the first exception and discards the
        winner — Milvus being down used to turn a perfectly good BM25/FTS
        result into a hard ``ExternalServiceError`` for the whole question.

        Degrading to the surviving leg is only acceptable if it is *visible*:
        every fallback logs at ERROR with the reason, ``/api/ready`` independently
        reports the dependency's status, and — since 2026-09-16 — the surviving
        leg is also reported to the caller in ``RetrievalDiagnostics``.  The log
        alone was not enough: an agent calling ``search_policy`` still received
        ``outcome: FOUND`` with no way to know the ranking had lost its semantic
        half.  If BOTH legs fail we re-raise instead of answering from an empty
        context — silently returning "no results" on a broken retrieval stack is
        worse than admitting failure.
        """
        dense_out, sparse_out = await asyncio.gather(
            self._dense(query, kb_id, tenant_id, settings.dense_top_k),
            self._sparse(query, kb_id, tenant_id, settings.sparse_top_k),
            return_exceptions=True,
        )

        dense: list[RetrievedChunk] = []
        sparse: list[RetrievedChunk] = []
        failed_legs: list[str] = []
        for leg, outcome in (("dense", dense_out), ("sparse", sparse_out)):
            if isinstance(outcome, BaseException):
                failed_legs.append(leg)
                logger.error(
                    "retrieval_leg_failed",
                    leg=leg,
                    kb_id=kb_id,
                    tenant_id=tenant_id,
                    error=str(outcome),
                    error_type=type(outcome).__name__,
                )
                continue
            if leg == "dense":
                dense = outcome
            else:
                sparse = outcome

        if not dense and not sparse:
            first = dense_out if isinstance(dense_out, BaseException) else sparse_out
            if isinstance(first, BaseException):
                raise first
            return [], RetrievalDiagnostics(strategy=RetrievalStrategy.HYBRID.value)

        # 只把**抛异常**的腿算作降级，不把"正常返回空"算作降级。
        # 两者必须分开：sparse 对纯语义的提问经常合法地 0 命中（例如英文提问、
        # 或库里没有对应字面的表述），那是窄查询而不是故障。若把它也标成降级，
        # 降级标记会出现在大量正常回答上，调用方很快就会学会忽略它 —— 恰好
        # 抵消了本次修复的意义。降级标记必须只在"依赖真的坏了"时出现。
        diags = RetrievalDiagnostics(strategy=RetrievalStrategy.HYBRID.value)
        if failed_legs:
            logger.error(
                "hybrid_retrieval_degraded_to_single_leg",
                kb_id=kb_id,
                dense_ok=bool(dense),
                sparse_ok=bool(sparse),
                failed_legs=failed_legs,
            )
            labels = "、".join(_LEG_LABEL.get(leg, leg) for leg in failed_legs)
            diags = RetrievalDiagnostics(
                strategy=RetrievalStrategy.HYBRID.value,
                degraded_legs=tuple(failed_legs),
                notes=(
                    f"{labels}检索本次不可用，结果排序已退化为仅依赖剩余的一种检索方式，"
                    "相关度排序可能不准；据此作答前请人工核对原文出处。",
                ),
            )

        # 来源级别必须在**融合前**回填：rrf_fusion 的平局裁决在排序时就要读它，
        # 融合之后再回填就晚了。对 dense+sparse 的并集只查一次 documents。
        await self._attach_authority(dense + sparse, tenant_id, kb_id=kb_id)

        return rrf_fusion(dense, sparse, k=settings.rrf_k, top_k=top_k), diags

    async def _rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
        """Reorder fused candidates by query↔chunk embedding similarity.

        Scope of this rerank (stated explicitly so callers do not over-trust it):
        it is a **bi-encoder** rerank that reuses the configured embedding model —
        no cross-encoder / dedicated rerank API is wired.  It reorders and scores
        candidates; it never adds or drops evidence, so citations stay complete.

        If the embedding service is unreachable the original fusion order is kept
        and the failure is logged — rerank is an ordering refinement, so losing it
        must not fail a legal-policy answer.
        """
        if len(chunks) < 2:
            return chunks
        try:
            vectors = await self._get_embedder().embed([query] + [c.content for c in chunks])
        except Exception as e:
            logger.warning("rerank_skipped_embedding_unavailable", error=repr(e), candidates=len(chunks))
            return chunks

        query_vector = vectors[0]
        scored: list[tuple[float, RetrievedChunk]] = []
        for chunk, vector in zip(chunks, vectors[1:], strict=True):
            similarity = _cosine_similarity(query_vector, vector)
            chunk.rerank_score = similarity
            scored.append((similarity, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        logger.info("rerank_applied", candidates=len(chunks), backend="bi-encoder-embedding")
        return [chunk for _, chunk in scored][:top_k]

    # --- helpers ---

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await self._get_embedder().embed([query])
        return vectors[0]

    async def _hydrate(
        self,
        hits: list[tuple[str, float]],
        kb_id: str,
        tenant_id: str,
        dense: bool,
    ) -> list[RetrievedChunk]:
        """Fetch chunk metadata from PostgreSQL for Milvus hit chunk_ids."""
        chunk_ids = [cid for cid, _ in hits]
        if not chunk_ids:
            return []

        stmt = (
            select(DocumentChunk, Document.filename)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.kb_id == kb_id,
                DocumentChunk.id.in_(chunk_ids),
            )
        )
        session = await make_tenant_session(tenant_id)
        try:
            rows = (await session.execute(stmt)).all()
        finally:
            await session.close()

        by_id = {chunk.id: (chunk, filename) for chunk, filename in rows}

        chunks: list[RetrievedChunk] = []
        for chunk_id, score in hits:
            pair = by_id.get(chunk_id)
            if pair is None:
                continue
            chunk, filename = pair
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=chunk.document_id,
                    kb_id=chunk.kb_id,
                    source=filename,
                    section=chunk.section or "",
                    content=chunk.content,
                    page_number=chunk.page_number,
                    score=float(score),
                    confidence=dense_confidence(float(score)),
                    dense_score=float(score) if dense else None,
                )
            )
        return chunks
