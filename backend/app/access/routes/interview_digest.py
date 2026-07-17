"""HRBP AI Workbench — Interview Digest API routes.

POST /api/interview-digest/upload → Upload interview document
POST /api/interview-digest/analyze → Start async analysis, returns task_id
GET  /api/interview-digest/progress/{task_id} → Poll task progress
GET  /api/interview-digest/result/{task_id} → Get completed result
GET  /api/interview-digest/history → Recent digest history
"""

import json
from fastapi import APIRouter, Request, UploadFile, File, HTTPException

from app.scenarios.interview_digest.orchestrator import InterviewDigestOrchestrator
from app.scenarios.interview_digest.schemas import DigestStatus, InterviewDigestResponse, UploadRequest
from app.access.middleware.decorators import require_auth, require_role
from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/interview-digest", tags=["interview-digest"])

orchestrator = InterviewDigestOrchestrator()


@router.post("/upload")
@require_auth
@require_role("hrbp")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
):
    """Upload an interview document (docx/pdf/txt) — returns parsed content."""
    tenant_id = getattr(request.state, "tenant_id", "default")

    # Validate file type
    allowed_types = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/pdf": "pdf",
        "text/plain": "txt",
    }
    content_type = file.content_type or "text/plain"

    if content_type not in allowed_types:
        raise ValidationError(f"不支持的文件类型: {content_type}")

    # Read file content
    content = await file.read()
    raw_text = ""

    if content_type == "text/plain":
        raw_text = content.decode("utf-8", errors="replace")
    elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        # TODO: Use python-docx for real parsing
        try:
            from docx import Document
            doc = Document(content)  # This needs a file-like object
            raw_text = "\n".join(para.text for para in doc.paragraphs)
        except Exception as e:
            logger.warning("docx_parse_failed", error=str(e))
            raw_text = f"[文档解析失败: {str(e)}]"
    elif content_type == "application/pdf":
        # TODO: Use pypdf for real parsing
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(content))
            raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            logger.warning("pdf_parse_failed", error=str(e))
            raw_text = f"[PDF解析失败: {str(e)}]"

    logger.info(
        "interview_document_uploaded",
        filename=file.filename,
        content_type=content_type,
        size_bytes=len(content),
        text_len=len(raw_text),
        tenant_id=tenant_id,
    )

    return {
        "filename": file.filename,
        "content_type": allowed_types[content_type],
        "text_length": len(raw_text),
        "content": raw_text,  # In production, store in MinIO and return a reference
    }


@router.post("/analyze")
@require_auth
@require_role("hrbp")
async def start_analysis(
    request: Request,
    body: dict,
):
    """Start async interview digest analysis — returns task_id."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    user_id = getattr(request.state, "user_id", "unknown")

    content = body.get("content", "")
    if not content or len(content) < 50:
        raise ValidationError("访谈内容过短，至少需要50字")

    task_id = await orchestrator.start_async_task(content, tenant_id, user_id)

    return {"task_id": task_id, "status": "pending"}


@router.get("/progress/{task_id}")
@require_auth
@require_role("hrbp")
async def get_progress(
    task_id: str,
    request: Request,
):
    """Poll async task progress."""
    status = orchestrator.get_task_status(task_id)
    if not status:
        raise NotFoundError("Task", task_id)

    return {
        "task_id": task_id,
        "status": status.status,
        "progress": status.progress,
        "error": status.error,
    }


@router.get("/result/{task_id}")
@require_auth
@require_role("hrbp")
async def get_result(
    task_id: str,
    request: Request,
):
    """Get completed interview digest result."""
    status = orchestrator.get_task_status(task_id)
    if not status:
        raise NotFoundError("Task", task_id)

    if status.status != "completed":
        raise HTTPException(status_code=425, detail=f"任务尚未完成，当前状态: {status.status}")

    return status.result


@router.get("/history")
@require_auth
@require_role("hrbp")
async def get_history(
    request: Request,
    limit: int = 20,
):
    """Get recent interview digest history."""
    # TODO: Query interview_digests table
    return {"digests": [], "total": 0}
