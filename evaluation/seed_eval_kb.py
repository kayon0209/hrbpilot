"""Seed the golden-eval retrieval layer with the synthetic policy corpus.

Drives the PRODUCTION ingestion path (MinIO -> DocumentParser -> section
Chunker -> EmbeddingClient -> PostgreSQL + Milvus) for tenant
``eval-runner`` / kb ``policy_kb`` — the exact ids the policy_qa scenario
config and ``evaluation/run_golden_eval.py`` use. The corpus is the
committed synthetic policy set: no real company policy, no employee data.

Usage: python evaluation/seed_eval_kb.py
Idempotent: replaces any previously seeded eval corpus version.
"""

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from app.data.database import make_tenant_session  # noqa: E402
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase  # noqa: E402
from app.evaluation.synthetic_policy_corpus import SYNTHETIC_POLICY_DOCS  # noqa: E402
from app.rag.embedding import get_embedder  # noqa: E402
from app.rag.ingestion.pipeline import IngestionService  # noqa: E402
from app.rag.storage.milvus import MilvusStore  # noqa: E402
from app.rag.storage.object_store import ObjectStore  # noqa: E402

TENANT = "eval-runner"
KB_ID = "policy_kb"


async def main() -> None:
    object_store = ObjectStore()
    await object_store.ensure_bucket_async()
    milvus = MilvusStore()
    await milvus.ensure_collection_async()
    service = IngestionService(milvus=milvus, object_store=object_store)

    session = await make_tenant_session(TENANT)
    try:
        # Idempotent replace of any previous eval corpus version.
        old = await session.execute(
            select(DocumentChunk.id).where(DocumentChunk.tenant_id == TENANT, DocumentChunk.kb_id == KB_ID)
        )
        old_ids = list(old.scalars().all())
        await session.execute(
            delete(DocumentChunk).where(DocumentChunk.tenant_id == TENANT, DocumentChunk.kb_id == KB_ID)
        )
        await session.execute(delete(Document).where(Document.tenant_id == TENANT, Document.kb_id == KB_ID))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.tenant_id == TENANT, KnowledgeBase.id == KB_ID))
        session.add(
            KnowledgeBase(
                id=KB_ID,
                tenant_id=TENANT,
                scenario_id="policy_qa",
                name="Golden eval synthetic policy corpus",
                chunk_strategy="section",
            )
        )
        await session.commit()
        if old_ids:
            with suppress(Exception):
                await milvus.delete_by_ids_async(old_ids)

        total_chunks = 0
        for doc in SYNTHETIC_POLICY_DOCS.values():
            payload = doc.full_text().encode("utf-8")
            s3_key = f"{TENANT}/{KB_ID}/{doc.filename}"
            await object_store.put_async(s3_key, payload, content_type="text/plain")
            document = Document(
                tenant_id=TENANT,
                kb_id=KB_ID,
                filename=doc.filename,
                s3_key=s3_key,
                file_type="txt",
                content_type="text/plain",
                size_bytes=len(payload),
                status="uploaded",
            )
            session.add(document)
            await session.flush()
            await service.process_document(document, session, chunk_strategy="section")
            n = (
                await session.execute(
                    select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
                )
            ).scalar_one()
            total_chunks += n
            print(f"  indexed {doc.filename}: {n} chunks")

        await milvus.flush_async()
        print(f"[done] tenant={TENANT} kb={KB_ID} docs={len(SYNTHETIC_POLICY_DOCS)} chunks={total_chunks}")
    finally:
        await session.close()
        with suppress(Exception):
            await get_embedder().aclose()


if __name__ == "__main__":
    asyncio.run(main())
