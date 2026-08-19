"""HRBP AI Workbench — document ingestion pipeline.

Real pipeline: Parse → Chunk → Tokenize → Embed → PostgreSQL chunks → Milvus.

Key invariants:
  - No zero-vector fallback: if embedding fails the document is marked error.
  - Rebuilding keeps the old Milvus vectors until the new PostgreSQL version
    commits. Failed rebuilds therefore leave the last good index available.
  - Only txt / pdf / docx are supported. .doc / .xls / .ppt are rejected.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.data.database import make_tenant_session
from app.data.models.infra import AsyncTask
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.rag.embedding import EmbeddingClient, get_embedder
from app.rag.retrieval.tokenizer import tokenize
from app.rag.storage.milvus import MilvusStore
from app.rag.storage.object_store import ObjectStore
from app.shared.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_TYPES = {"txt", "pdf", "docx"}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocumentParser:
    """Parse uploaded files into plain text. Raises on any failure."""

    def parse(self, content: bytes, file_type: str) -> str:
        ft = (file_type or "").lower().lstrip(".")
        if ft not in SUPPORTED_TYPES:
            raise ValueError(f"Unsupported file type: {file_type}. Supported: {', '.join(sorted(SUPPORTED_TYPES))}")

        if ft == "txt":
            return content.decode("utf-8", errors="replace")

        if ft == "docx":
            from docx import Document as DocxDocument

            doc = DocxDocument(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        # pdf
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)


class Chunker:
    """Split parsed text into chunks for embedding."""

    def chunk(
        self,
        text: str,
        strategy: str = "fixed_512",
        chunk_size: int = 512,
        overlap: int = 50,
        source: str = "",
    ) -> list[dict[str, Any]]:
        """Split text into overlapping chunks (or by section)."""
        if strategy == "section":
            return self._chunk_by_section(text, source)

        if chunk_size <= overlap:
            overlap = max(0, chunk_size // 10)

        chunks: list[dict[str, Any]] = []
        text_len = len(text)
        step = max(1, chunk_size - overlap)
        for i in range(0, text_len, step):
            chunk_text = text[i : i + chunk_size]
            if not chunk_text.strip():
                continue
            chunks.append(
                {
                    "content": chunk_text.strip(),
                    "index": len(chunks),
                    "section": f"片段 {len(chunks) + 1}",
                    "start_char": i,
                    "end_char": min(i + chunk_size, text_len),
                }
            )
        return chunks

    def _chunk_by_section(self, text: str, source: str) -> list[dict[str, Any]]:
        pattern = r"(第[一二三四五六七八九十\d]+[章节条]|[一二三四五六七八九十\d]+、|\d+\.\d+)"
        parts = re.split(pattern, text)
        chunks: list[dict[str, Any]] = []
        current_section = "总则"
        current_text = ""
        current_start = 0
        cursor = 0

        for part in parts:
            if re.match(pattern, part):
                if current_text.strip():
                    chunks.append(
                        {
                            "content": current_text.strip(),
                            "index": len(chunks),
                            "section": current_section,
                            "start_char": current_start,
                            "end_char": current_start + len(current_text),
                        }
                    )
                current_section = part.strip()
                current_text = part
                current_start = cursor
            else:
                current_text += part
            cursor += len(part)

        if current_text.strip():
            chunks.append(
                {
                    "content": current_text.strip(),
                    "index": len(chunks),
                    "section": current_section,
                    "start_char": current_start,
                    "end_char": current_start + len(current_text),
                }
            )

        return chunks if chunks else self.chunk(text, source=source)


class IngestionService:
    """Parse → chunk → tokenize → embed → PG → Milvus, with atomic rebuild."""

    def __init__(
        self,
        embedder: EmbeddingClient | None = None,
        milvus: MilvusStore | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self.parser = DocumentParser()
        self.chunker = Chunker()
        self._embedder = embedder
        self._milvus = milvus
        self._object_store = object_store

    def _get_embedder(self) -> EmbeddingClient:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def _get_milvus(self) -> MilvusStore:
        if self._milvus is None:
            self._milvus = MilvusStore()
        return self._milvus

    def _get_object_store(self) -> ObjectStore:
        if self._object_store is None:
            self._object_store = ObjectStore()
        return self._object_store

    async def process_document(
        self,
        document: Document,
        session: AsyncSession,
        chunk_strategy: str = "fixed_512",
        chunk_size: int = 512,
    ) -> None:
        """Ingest a single document into PostgreSQL + Milvus.

        Raises on failure; the caller marks the document as error. New vectors
        are compensated by exact id if the PostgreSQL commit fails. Old vectors
        are removed only after that commit succeeds.
        """
        doc_id = document.id
        kb_id = document.kb_id
        tenant_id = document.tenant_id

        # 1. Fetch raw bytes from object storage
        content = await self._get_object_store().get_async(document.s3_key)

        # 2. Parse
        text = await asyncio.to_thread(self.parser.parse, content, document.file_type)
        if not text.strip():
            raise ValueError("Parsed document produced empty text")

        # 3. Chunk
        raw_chunks = self.chunker.chunk(text, strategy=chunk_strategy, chunk_size=chunk_size, source=document.filename)
        if not raw_chunks:
            raise ValueError("No chunks produced from document")

        # 4. Embed (raises on failure — no zero-vector fallback)
        contents = [c["content"] for c in raw_chunks]
        embeddings = await self._get_embedder().embed(contents)
        if len(embeddings) != len(contents):
            raise ValueError(f"Embedding count {len(embeddings)} != chunk count {len(contents)}")

        # 5. Record the last good vector ids, then replace PG chunks inside the
        # current transaction. Old Milvus rows remain searchable until commit.
        old_result = await session.execute(select(DocumentChunk.id).where(DocumentChunk.document_id == doc_id))
        old_chunk_ids = list(old_result.scalars().all()) if old_result is not None else []
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))

        # 6. Write new chunks to PostgreSQL + build Milvus rows
        milvus_rows: list[dict[str, Any]] = []
        for c, emb in zip(raw_chunks, embeddings, strict=False):
            chunk_id = str(uuid4())
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    document_id=doc_id,
                    chunk_index=c["index"],
                    content=c["content"],
                    keyword_text=tokenize(c["content"]),
                    section=c["section"],
                    start_char=c["start_char"],
                    end_char=c["end_char"],
                    content_sha256=sha256_hex(c["content"].encode("utf-8")),
                    embedding_model=settings.embedding_model,
                    status="active",
                )
            )
            milvus_rows.append(
                {
                    "chunk_id": chunk_id,
                    "tenant_id": tenant_id,
                    "kb_id": kb_id,
                    "document_id": doc_id,
                    "embedding": emb,
                }
            )

        # Surface PostgreSQL constraint/serialization errors before creating
        # vectors in Milvus.
        await session.flush()

        # 7. Upsert to Milvus (if this fails, roll back the PG chunk writes)
        try:
            await self._get_milvus().upsert_async(milvus_rows)
        except Exception:
            await session.rollback()
            raise

        # 8. Mark indexed
        document.status = "indexed"
        document.error_message = None
        document.indexed_at = datetime.now(UTC)
        try:
            await session.commit()
        except Exception:
            # PostgreSQL and Milvus cannot share a transaction. Compensate only
            # the new version; deleting by document would also erase last-good
            # vectors that the rolled-back PostgreSQL rows still reference.
            try:
                await self._get_milvus().delete_by_ids_async([str(row["chunk_id"]) for row in milvus_rows])
            except Exception as cleanup_error:
                logger.error(
                    "ingestion_milvus_compensation_failed",
                    document_id=doc_id,
                    error=str(cleanup_error),
                )
            await session.rollback()
            raise

        # The new PG version is now authoritative. Cleanup failure is harmless
        # to recall correctness: hydration drops old vector ids whose PG rows no
        # longer exist, and a later maintenance pass can retry the deletion.
        try:
            await self._get_milvus().delete_by_ids_async(old_chunk_ids)
        except Exception as cleanup_error:
            logger.warning(
                "ingestion_old_vector_cleanup_failed",
                document_id=doc_id,
                error=str(cleanup_error),
            )

        logger.info("ingestion_document_indexed", document_id=doc_id, chunks=len(milvus_rows))


async def run_ingestion_task(task_id: str, tenant_id: str) -> None:
    """Background worker: process all pending documents for one AsyncTask."""
    session = await make_tenant_session(tenant_id)
    service = IngestionService()
    try:
        task = (await session.execute(select(AsyncTask).where(AsyncTask.id == task_id))).scalar_one_or_none()
        if task is None:
            logger.error("ingestion_task_not_found", task_id=task_id)
            return

        payload = json.loads(task.result_json or "{}")
        kb_id = payload.get("kb_id", "")
        document_ids = payload.get("document_ids", [])
        kb = (
            await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if kb is None:
            raise ValueError(f"Knowledge base {kb_id} not found for ingestion task")

        task.status = "running"
        task.started_at = datetime.now(UTC)
        await session.commit()

        total = len(document_ids)
        processed = 0
        succeeded = 0
        failed = 0
        skipped = 0
        for doc_id in document_ids:
            document = (
                await session.execute(
                    select(Document).where(
                        Document.id == doc_id,
                        Document.tenant_id == tenant_id,
                        Document.kb_id == kb_id,
                        Document.status == "parsing",
                    )
                )
            ).scalar_one_or_none()
            if document is None:
                processed += 1
                skipped += 1
                task.progress = int(processed / total * 100) if total else 100
                await session.commit()
                logger.warning("ingestion_document_skipped", document_id=doc_id, task_id=task_id)
                continue
            try:
                await service.process_document(
                    document, session, chunk_strategy=kb.chunk_strategy, chunk_size=kb.chunk_size
                )
            except Exception as e:
                document.status = "error"
                document.error_message = str(e)[:2000]
                await session.commit()
                logger.error("ingestion_document_failed", document_id=doc_id, error=str(e))
                failed += 1
            else:
                succeeded += 1
            processed += 1
            task.progress = int(processed / total * 100) if total else 100
            await session.commit()

        # Avoid sealing a tiny segment for every document. A single batch flush
        # preserves predictable visibility while keeping Milvus segment churn low.
        if succeeded:
            await service._get_milvus().flush_async()

        if succeeded == 0 and (failed or skipped):
            task.status = "failed"
        elif failed or skipped:
            task.status = "partial"
        else:
            task.status = "completed"
        task.progress = 100
        task.completed_at = datetime.now(UTC)
        task.result_json = json.dumps(
            {"kb_id": kb_id, "processed": processed, "succeeded": succeeded, "failed": failed, "skipped": skipped}
        )
        task.error_message = f"{failed} document(s) failed; {skipped} skipped" if failed or skipped else None
        await session.commit()
        logger.info(
            "ingestion_task_completed",
            task_id=task_id,
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
        )
    except Exception as e:
        logger.error("ingestion_task_failed", task_id=task_id, error=str(e))
        try:
            task = (await session.execute(select(AsyncTask).where(AsyncTask.id == task_id))).scalar_one_or_none()
            if task is not None:
                task.status = "failed"
                task.error_message = str(e)[:2000]
                task.completed_at = datetime.now(UTC)
                await session.commit()
        except Exception:
            pass
    finally:
        await session.close()
