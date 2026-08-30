"""HRBP AI Workbench — Knowledge Base management API routes.

POST   /api/kb/create                        → create KB (PostgreSQL)
GET    /api/kb/list                          → list KBs for tenant
GET    /api/kb/{kb_id}                       → KB details
POST   /api/kb/{kb_id}/upload                → upload doc → MinIO + Document
POST   /api/kb/{kb_id}/ingest                → trigger async ingestion task
GET    /api/kb/{kb_id}/documents             → list docs + chunk counts
DELETE /api/kb/{kb_id}/documents/{doc_id}    → delete doc (MinIO + PG + Milvus)
POST   /api/kb/delete                        → delete KB (MinIO + PG + Milvus)
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.infra import AsyncTask
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.rag.ingestion.pipeline import SUPPORTED_TYPES, sha256_hex
from app.rag.ingestion.tasks import dispatch_ingestion_task
from app.rag.storage.milvus import MilvusStore
from app.rag.storage.object_store import ObjectStore
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


class CreateKBBody(BaseModel):
    name: str = ""
    scenario_id: str = ""
    description: str = ""
    chunk_strategy: str = "fixed_512"


class DeleteKBBody(BaseModel):
    kb_id: str


def _kb_to_dict(kb: KnowledgeBase, document_count: int = 0, total_chunks: int = 0) -> dict[str, Any]:
    return {
        "id": kb.id,
        "tenant_id": kb.tenant_id,
        "name": kb.name,
        "scenario_id": kb.scenario_id,
        "chunk_strategy": kb.chunk_strategy,
        "chunk_size": kb.chunk_size,
        "status": kb.status,
        "document_count": document_count,
        "total_chunks": total_chunks,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    }


def _doc_to_dict(doc: Document, chunk_count: int = 0) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "tenant_id": doc.tenant_id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "size_bytes": doc.size_bytes,
        "content_sha256": doc.content_sha256,
        "status": doc.status,
        "error_message": doc.error_message,
        "chunk_count": chunk_count,
        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


async def _sweep_stale_ingestion(session: AsyncSession, tenant_id: str, kb_id: str) -> None:
    """Surface dead ingestion runs honestly (audit 2026-08-31 P0-1).

    A worker that died mid-run used to leave its async task in ``pending``
    forever and its documents stuck in ``parsing`` with no explanation. This
    sweep (a) expires stale tasks for the caller's tenant (FORCE RLS requires
    an explicit tenant context) and (b) returns documents claimed by a failed
    ingestion back to ``error`` with a recoverable message so the import can
    be retried.
    """
    from app.scenarios.tasks import expire_stale_tasks

    await expire_stale_tasks(tenant_id)
    failed_tasks = (
        (
            await session.execute(
                select(AsyncTask).where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type == "document_ingestion",
                    AsyncTask.status == "failed",
                )
            )
        )
        .scalars()
        .all()
    )
    stale_doc_ids: list[str] = []
    for task in failed_tasks:
        try:
            payload = json.loads(task.result_json or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("kb_id") != kb_id:
            continue
        stale_doc_ids.extend(payload.get("document_ids") or [])
    if stale_doc_ids:
        await session.execute(
            update(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.kb_id == kb_id,
                Document.id.in_(stale_doc_ids),
                Document.status == "parsing",
            )
            .values(status="error", error_message="导入任务超时未完成，请重新建立索引。")
        )
        await session.commit()


@router.post("/create")
@require_auth
@require_capability("kb_management")
async def create_kb(
    body: CreateKBBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = KnowledgeBase(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=body.name or "未命名知识库",
        scenario_id=body.scenario_id,
        chunk_strategy=body.chunk_strategy,
        chunk_size=512,
        status="active",
    )
    session.add(kb)
    await session.flush()
    logger.info("kb_created", kb_id=kb.id, name=kb.name, tenant_id=tenant_id)
    return _kb_to_dict(kb)


@router.get("/list")
@require_auth
async def list_kbs(
    request: Request,
    scenario_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    stmt = select(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id)
    if scenario_id:
        stmt = stmt.where(KnowledgeBase.scenario_id == scenario_id)
    kbs = (await session.execute(stmt)).scalars().all()

    counts = (
        await session.execute(
            select(
                Document.kb_id,
                func.count(func.distinct(Document.id)).label("document_count"),
                func.count(DocumentChunk.id).label("total_chunks"),
            )
            .select_from(Document)
            .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(Document.tenant_id == tenant_id)
            .group_by(Document.kb_id)
        )
    ).all()
    count_map = {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in counts}

    return {
        "knowledge_bases": [
            _kb_to_dict(
                kb, document_count=count_map.get(kb.id, (0, 0))[0], total_chunks=count_map.get(kb.id, (0, 0))[1]
            )
            for kb in kbs
        ],
        "total": len(kbs),
    }


@router.get("/{kb_id}")
@require_auth
async def get_kb(
    kb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("KnowledgeBase", kb_id)

    counts = (
        await session.execute(
            select(func.count(func.distinct(Document.id)), func.count(DocumentChunk.id))
            .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(Document.kb_id == kb_id)
        )
    ).one()
    return _kb_to_dict(kb, document_count=int(counts[0]), total_chunks=int(counts[1] or 0))


@router.post("/{kb_id}/upload")
@require_auth
@require_capability("kb_management")
async def upload_document(
    kb_id: str,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("KnowledgeBase", kb_id)

    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_TYPES:
        raise ValidationError(f"不支持的文件类型 .{ext or '(无扩展名)'}；仅支持 {', '.join(sorted(SUPPORTED_TYPES))}")

    content = await file.read()
    if len(content) == 0:
        raise ValidationError("文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    content_sha256 = sha256_hex(content)

    # Hash dedup — never silently create a duplicate index for the same bytes.
    existing = (
        (
            await session.execute(
                select(Document).where(Document.kb_id == kb_id, Document.content_sha256 == content_sha256)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise ConflictError(
            f"相同内容的文件已存在（doc_id={existing.id}），跳过重复上传",
            resource="document",
        )

    doc_id = str(uuid.uuid4())
    s3_key = f"{kb_id}/{doc_id}/{filename or 'document'}"

    store = ObjectStore()
    await store.ensure_bucket_async()
    await store.put_async(s3_key, content, content_type=file.content_type or "application/octet-stream")

    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        kb_id=kb_id,
        filename=filename,
        s3_key=s3_key,
        file_type=ext,
        content_type=file.content_type,
        size_bytes=len(content),
        content_sha256=content_sha256,
        status="uploaded",
    )
    session.add(doc)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        try:
            await store.delete_async(s3_key)
        except Exception as cleanup_error:
            logger.error("orphan_upload_cleanup_failed", key=s3_key, error=str(cleanup_error))
        raise ConflictError("相同内容的文件已存在，跳过重复上传", resource="document") from exc
    except Exception:
        await session.rollback()
        try:
            await store.delete_async(s3_key)
        except Exception as cleanup_error:
            logger.error("orphan_upload_cleanup_failed", key=s3_key, error=str(cleanup_error))
        raise
    logger.info("kb_document_uploaded", kb_id=kb_id, doc_id=doc_id, filename=filename)
    return _doc_to_dict(doc)


@router.post("/{kb_id}/ingest")
@require_auth
@require_capability("kb_management")
async def trigger_ingestion(
    kb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("KnowledgeBase", kb_id)

    # Dead-run sweep BEFORE claiming: previously stuck documents (from a task
    # whose worker died) return to 'error' here so this retry can pick them up.
    await _sweep_stale_ingestion(session, tenant_id, kb_id)

    # Atomic claim: concurrent requests cannot enqueue the same document twice.
    claimed_ids = list(
        (
            await session.execute(
                update(Document)
                .where(
                    Document.kb_id == kb_id,
                    Document.tenant_id == tenant_id,
                    Document.status.in_(["uploaded", "error"]),
                )
                .values(status="parsing", error_message=None)
                .returning(Document.id)
            )
        )
        .scalars()
        .all()
    )
    if not claimed_ids:
        return {"task_id": None, "status": "noop", "message": "没有待处理的文档"}

    task = AsyncTask(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        type="document_ingestion",
        status="pending",
        progress=0,
        result_json=json.dumps({"kb_id": kb_id, "document_ids": claimed_ids}),
    )
    session.add(task)
    await session.flush()
    # The worker opens a separate session.  Commit the task first so it cannot
    # race this request transaction and observe a missing task row.
    await session.commit()

    try:
        dispatch_ingestion_task(task.id, tenant_id)
    except Exception as exc:
        await session.execute(update(Document).where(Document.id.in_(claimed_ids)).values(status="uploaded"))
        task.status = "failed"
        task.error_message = f"Failed to dispatch ingestion worker: {exc}"[:2000]
        await session.commit()
        raise
    logger.info("kb_ingestion_started", kb_id=kb_id, task_id=task.id, documents=len(claimed_ids))
    return {"task_id": task.id, "status": "processing", "documents": len(claimed_ids)}


@router.get("/{kb_id}/documents")
@require_auth
async def list_documents(
    kb_id: str,
    request: Request,
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("KnowledgeBase", kb_id)

    # Honesty sweep: a document left 'parsing' by a dead worker must surface
    # as error here, not hang in the list forever (audit 2026-08-31 P0-1).
    await _sweep_stale_ingestion(session, tenant_id, kb_id)

    docs = (
        (
            await session.execute(
                select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    chunk_counts: dict[str, int] = {
        str(row[0]): int(row[1])
        for row in (
            await session.execute(
                select(DocumentChunk.document_id, func.count(DocumentChunk.id))
                .where(DocumentChunk.kb_id == kb_id)
                .group_by(DocumentChunk.document_id)
            )
        ).fetchall()
    }

    return {
        "documents": [_doc_to_dict(d, chunk_count=int(chunk_counts.get(d.id, 0))) for d in docs],
        "total": len(docs),
    }


@router.delete("/{kb_id}/documents/{doc_id}")
@require_auth
@require_capability("kb_management")
async def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    doc = (
        await session.execute(
            select(Document).where(Document.id == doc_id, Document.kb_id == kb_id, Document.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("Document", doc_id)

    scope: dict[str, Any] = {"document_id": doc_id, "kb_id": kb_id, "minio_key": doc.s3_key}

    # Commit the authoritative metadata deletion first. If external cleanup
    # fails, stale vectors cannot hydrate and the object is no longer exposed.
    await session.delete(doc)
    await session.commit()

    try:
        await ObjectStore().delete_async(doc.s3_key)
    except Exception as e:
        logger.warning("minio_delete_failed", key=doc.s3_key, error=str(e))

    try:
        removed_vectors = await MilvusStore().delete_by_document_async(doc_id)
    except Exception as e:
        removed_vectors = 0
        logger.warning("milvus_delete_failed", document_id=doc_id, error=str(e))

    scope["removed_vectors"] = removed_vectors
    logger.info("kb_document_deleted", kb_id=kb_id, doc_id=doc_id, vectors=removed_vectors)
    return {"status": "deleted", **scope}


@router.post("/delete")
@require_auth
@require_capability("kb_management")
async def delete_kb(
    body: DeleteKBBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    kb = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == body.kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("KnowledgeBase", body.kb_id)

    docs = (await session.execute(select(Document).where(Document.kb_id == body.kb_id))).scalars().all()

    object_keys = [doc.s3_key for doc in docs]
    await session.execute(delete(Document).where(Document.kb_id == body.kb_id, Document.tenant_id == tenant_id))
    await session.delete(kb)
    await session.commit()

    store = ObjectStore()
    for object_key in object_keys:
        try:
            await store.delete_async(object_key)
        except Exception as e:
            logger.warning("minio_delete_failed", key=object_key, error=str(e))
    try:
        removed_vectors = await MilvusStore().delete_by_kb_async(body.kb_id)
    except Exception as e:
        removed_vectors = 0
        logger.warning("milvus_delete_failed", kb_id=body.kb_id, error=str(e))

    logger.info("kb_deleted", kb_id=body.kb_id, documents=len(docs), vectors=removed_vectors)
    return {"status": "deleted", "kb_id": body.kb_id, "documents": len(docs), "removed_vectors": removed_vectors}
