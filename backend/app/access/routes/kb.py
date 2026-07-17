"""HRBP AI Workbench — Knowledge Base management API routes.

POST /api/kb/create → Create a new knowledge base
GET  /api/kb/list → List knowledge bases for tenant
GET  /api/kb/{kb_id} → Get KB details
POST /api/kb/{kb_id}/upload → Upload document to KB
POST /api/kb/{kb_id}/ingest → Trigger ingestion pipeline
GET  /api/kb/{kb_id}/documents → List documents in KB
DELETE /api/kb/{kb_id}/documents/{doc_id} → Delete a document
"""

import uuid
from fastapi import APIRouter, Request, UploadFile, File
from pydantic import BaseModel

from app.rag.ingestion.pipeline import IngestionPipeline
from app.access.middleware.decorators import require_auth, require_role
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

ingestion = IngestionPipeline()

# In-memory KB store (replace with DB in production)
_kb_store: dict[str, dict] = {}


class CreateKBBody(BaseModel):
    name: str = ""
    scenario_id: str = ""
    description: str = ""
    chunk_strategy: str = "fixed_512"


@router.post("/create")
@require_auth
@require_role("hr_manager")
async def create_kb(
    request: Request,
    body: CreateKBBody,
):
    """Create a new knowledge base."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    kb_id = str(uuid.uuid4())
    kb = {
        "id": kb_id,
        "tenant_id": tenant_id,
        "name": body.name,
        "scenario_id": body.scenario_id,
        "description": body.description,
        "chunk_strategy": body.chunk_strategy,
        "status": "active",
        "document_count": 0,
        "total_chunks": 0,
    }
    _kb_store[kb_id] = kb

    logger.info("kb_created", kb_id=kb_id, name=kb["name"], tenant_id=tenant_id)
    return kb


@router.get("/list")
@require_auth
async def list_kbs(
    request: Request,
    scenario_id: str | None = None,
):
    """List knowledge bases for the current tenant."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    kbs = [kb for kb in _kb_store.values() if kb["tenant_id"] == tenant_id]
    if scenario_id:
        kbs = [kb for kb in kbs if kb["scenario_id"] == scenario_id]
    return {"knowledge_bases": kbs, "total": len(kbs)}


@router.get("/{kb_id}")
@require_auth
async def get_kb(kb_id: str, request: Request):
    """Get knowledge base details."""
    kb = _kb_store.get(kb_id)
    if not kb:
        raise NotFoundError("KnowledgeBase", kb_id)
    return kb


@router.post("/{kb_id}/upload")
@require_auth
@require_role("hr_manager")
async def upload_document(
    kb_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """Upload a document to a knowledge base."""
    kb = _kb_store.get(kb_id)
    if not kb:
        raise NotFoundError("KnowledgeBase", kb_id)

    content = await file.read()

    doc_id = str(uuid.uuid4())
    doc = {
        "id": doc_id,
        "kb_id": kb_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
        "status": "uploaded",
        "indexed_at": None,
    }

    kb["document_count"] += 1

    logger.info("kb_document_uploaded", kb_id=kb_id, doc_id=doc_id, filename=file.filename)
    return doc


@router.post("/{kb_id}/ingest")
@require_auth
@require_role("hr_manager")
async def trigger_ingestion(
    kb_id: str,
    request: Request,
):
    """Trigger the ingestion pipeline for a knowledge base."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    kb = _kb_store.get(kb_id)
    if not kb:
        raise NotFoundError("KnowledgeBase", kb_id)

    task_id = await ingestion.start_ingestion(kb_id, tenant_id)

    logger.info("kb_ingestion_started", kb_id=kb_id, task_id=task_id)
    return {"task_id": task_id, "status": "processing"}


@router.get("/{kb_id}/documents")
@require_auth
async def list_documents(
    kb_id: str,
    request: Request,
    limit: int = 50,
):
    """List documents in a knowledge base."""
    return {"documents": [], "total": 0}


class DeleteKBBody(BaseModel):
    kb_id: str


@router.post("/delete")
@require_auth
@require_role("hr_manager")
async def delete_kb(body: DeleteKBBody, request: Request):
    kb = _kb_store.pop(body.kb_id, None)
    if not kb:
        raise NotFoundError("KnowledgeBase", body.kb_id)
    logger.info("kb_deleted", kb_id=body.kb_id, name=kb.get("name", ""))
    return {"status": "deleted", "kb_id": body.kb_id}


@router.delete("/{kb_id}/documents/{doc_id}")
@require_auth
@require_role("hr_manager")
async def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
):
    """Delete a document from a knowledge base."""
    logger.info("kb_document_deleted", kb_id=kb_id, doc_id=doc_id)
    return {"status": "deleted"}
