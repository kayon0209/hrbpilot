"""Integration test — real PostgreSQL + Milvus hybrid RAG end-to-end.

Uploads a small Chinese policy document, indexes it (chunks -> PostgreSQL +
Milvus), then retrieves with:
  - sparse (exact clause keywords)
  - dense  (vector search)
  - hybrid (RRF fusion)

Requires Milvus + PostgreSQL reachable (see docker-compose.yml). Skips cleanly
when they are not. If EMBEDDING_API_KEY is set, real cloud embeddings are used
(semantic paraphrase matching); otherwise a deterministic embedding is used to
exercise the full plumbing (non-zero, reproducible vectors).
"""

import hashlib
import math
import socket
import uuid

import pytest
from sqlalchemy import text

from app.config.settings import settings
from app.data.database import get_engine, make_tenant_session
from app.data.models.knowledge_base import Document, KnowledgeBase
from app.rag.ingestion.pipeline import IngestionService, sha256_hex
from app.rag.retrieval.retriever import Retriever
from app.rag.storage.milvus import MilvusStore

pytestmark = pytest.mark.integration

POLICY_TEXT = (
    "第3.1条 工作时间：公司实行标准工时制，工作日为周一至周五，每日工作8小时。\n"
    "第3.3条 请假流程：员工请假需提前在OA系统提交申请，1天以内由直接主管审批，"
    "1-3天由部门负责人审批，3天以上需HR总监审批。病假需提供医院证明。\n"
    "第5.2条 年假标准：入职满1年享有5天年假，满3年享有10天，满5年享有15天。"
)


def _milvus_reachable() -> bool:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((settings.vector_db_host, settings.vector_db_port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _deterministic_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for i in range(len(text) - 1):
        h = int(hashlib.md5(text[i : i + 2].encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class _DeterministicEmbedder:
    def __init__(self, dim: int):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(t, self.dim) for t in texts]


class _FakeStore:
    def __init__(self, content: bytes):
        self.content = content

    async def get_async(self, key: str) -> bytes:
        return self.content


def _build_embedder():
    if settings.embedding_api_key and settings.embedding_base_url:
        from app.rag.embedding import get_embedder

        return get_embedder()
    return _DeterministicEmbedder(settings.embedding_dimension)


async def test_hybrid_rag_end_to_end():
    if not _milvus_reachable():
        pytest.skip("Milvus not reachable — start it with `docker compose up`")

    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL connection is not configured for integration tests: {exc}")

    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    kb_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    collection = f"test_hrbp_{uuid.uuid4().hex[:8]}"

    embedder = _build_embedder()
    milvus = MilvusStore(collection_name=collection, dim=settings.embedding_dimension)
    await milvus.ensure_collection_async()

    store = _FakeStore(POLICY_TEXT.encode("utf-8"))
    service = IngestionService(embedder=embedder, milvus=milvus, object_store=store)
    retriever = Retriever(embedder=embedder, milvus=milvus)

    # Set up PG: KB + Document
    session = await make_tenant_session(tenant_id)
    try:
        session.add(
            KnowledgeBase(
                id=kb_id,
                tenant_id=tenant_id,
                scenario_id="policy_qa",
                name="测试制度库",
                chunk_strategy="fixed_512",
                chunk_size=512,
                status="active",
            )
        )
        # Flush the KB before adding its Document: a bare ForeignKey does not
        # order the unit of work, so a single flush can emit the child INSERT
        # first and violate fk_documents_kb_id (production flushes in stages too).
        await session.flush()
        session.add(
            Document(
                id=doc_id,
                tenant_id=tenant_id,
                kb_id=kb_id,
                filename="员工手册.txt",
                s3_key=f"{kb_id}/{doc_id}/员工手册.txt",
                file_type="txt",
                size_bytes=len(POLICY_TEXT.encode("utf-8")),
                content_sha256=sha256_hex(POLICY_TEXT.encode("utf-8")),
                status="uploaded",
            )
        )
        await session.commit()

        doc = (
            await session.execute(__import__("sqlalchemy").select(Document).where(Document.id == doc_id))
        ).scalar_one()

        # Ingest -> chunks in PG + vectors in Milvus
        await service.process_document(doc, session)

        try:
            # 1. sparse — exact clause keywords
            sparse = await retriever._sparse("请假流程 OA审批", kb_id, tenant_id, 5)
            assert len(sparse) >= 1, "sparse should hit the 请假 chunk"
            assert any("请假" in c.content for c in sparse)

            # 2. dense — exact text (works with both deterministic + real embedder)
            dense = await retriever._dense("第3.3条 请假流程：员工请假需提前在OA系统提交申请", kb_id, tenant_id, 5)
            assert len(dense) >= 1, "dense should hit at least one chunk"

            # 3. hybrid — non-empty and tenant/kb scoped
            hybrid = await retriever._hybrid("请假流程审批", kb_id, tenant_id, 5)
            assert len(hybrid) >= 1
            for c in hybrid:
                assert c.kb_id == kb_id

            # 4. tenant isolation — another tenant sees nothing
            other = await retriever._sparse("请假流程", kb_id, "other-tenant", 5)
            assert other == []
            other_dense = await retriever._dense("请假流程", kb_id, "other-tenant", 5)
            assert other_dense == []

            # 5. exact clause (keyword) present in hybrid or sparse
            exact = await retriever._sparse("年假标准 入职满1年", kb_id, tenant_id, 5)
            assert any("年假" in c.content for c in exact)
        finally:
            await milvus.delete_by_kb_async(kb_id)
    finally:
        await session.close()
