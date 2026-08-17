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
from typing import Any

from sqlalchemy import select, text

from app.config.settings import settings
from app.data.database import make_tenant_session
from app.data.models.knowledge_base import Document, DocumentChunk
from app.rag.config_loader import RetrievalStrategy
from app.rag.embedding import EmbeddingClient, get_embedder
from app.rag.retrieval.fusion import dense_confidence, rrf_fusion, sparse_confidence
from app.rag.retrieval.tokenizer import tokenize_for_query
from app.rag.retrieval.types import RetrievedChunk
from app.rag.storage.milvus import MilvusStore
from app.shared.logger import get_logger

logger = get_logger(__name__)


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
        """
        if isinstance(strategy, str):
            strategy = RetrievalStrategy(strategy)

        if rerank:
            logger.warning(
                "rerank_requested_but_not_implemented",
                strategy=strategy.value,
                note="rerank=True keeps the interface only; no cross-encoder/rerank API wired",
            )

        logger.info(
            "retrieval_requested",
            query=query[:50],
            kb_id=kb_id,
            strategy=strategy.value,
            top_k=top_k,
            tenant_id=tenant_id,
        )

        if strategy is RetrievalStrategy.DENSE:
            chunks = await self._dense(query, kb_id, tenant_id, top_k)
        elif strategy is RetrievalStrategy.SPARSE:
            chunks = await self._sparse(query, kb_id, tenant_id, top_k)
        else:
            chunks = await self._hybrid(query, kb_id, tenant_id, top_k)

        logger.info("retrieval_completed", kb_id=kb_id, strategy=strategy.value, count=len(chunks))
        return [c.to_dict() for c in chunks]

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
                    score=float(row.rank or 0.0),
                    confidence=sparse_confidence(float(row.rank or 0.0)),
                    sparse_score=float(row.rank or 0.0),
                )
            )
        return chunks[:top_k]

    async def _hybrid(self, query: str, kb_id: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        dense, sparse = await asyncio.gather(
            self._dense(query, kb_id, tenant_id, settings.dense_top_k),
            self._sparse(query, kb_id, tenant_id, settings.sparse_top_k),
        )
        return rrf_fusion(dense, sparse, k=settings.rrf_k, top_k=top_k)

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
                    score=float(score),
                    confidence=dense_confidence(float(score)),
                    dense_score=float(score) if dense else None,
                )
            )
        return chunks
