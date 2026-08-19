"""HRBP AI Workbench — Interview Digest API routes.

POST /api/interview-digest/upload → Upload interview document
POST /api/interview-digest/analyze → Start async analysis, returns task_id
GET  /api/interview-digest/progress/{task_id} → Poll task progress
GET  /api/interview-digest/result/{task_id} → Get completed result
GET  /api/interview-digest/history → Recent digest history
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.infra import AsyncTask
from app.scenarios.interview_digest.orchestrator import InterviewDigestOrchestrator
from app.scenarios.interview_digest.schemas import InterviewDigestResponse
from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/interview-digest", tags=["interview-digest"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — same limit as the KB upload route

orchestrator = InterviewDigestOrchestrator()


@router.post("/upload")
@require_auth
@require_role("hrbp")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
):
    """Upload an interview document (docx/pdf/txt) — returns parsed content."""
    tenant_id = require_tenant_id(request)

    # Validate file type
    allowed_types = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/pdf": "pdf",
        "text/plain": "txt",
    }
    content_type = file.content_type or "text/plain"

    if content_type not in allowed_types:
        raise ValidationError(f"不支持的文件类型: {content_type}")

    # Read file content — enforce a size cap so oversized uploads fail fast
    # instead of exhausting memory.
    content = await file.read()
    if len(content) == 0:
        raise ValidationError("文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    raw_text = ""

    if content_type == "text/plain":
        raw_text = content.decode("utf-8", errors="replace")
    elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            import io

            from docx import Document

            doc = Document(io.BytesIO(content))
            raw_text = "\n".join(para.text for para in doc.paragraphs)
        except Exception as e:
            logger.warning("docx_parse_failed", error=str(e))
            raw_text = f"[文档解析失败: {e!s}]"
    elif content_type == "application/pdf":
        # TODO: Use pypdf for real parsing
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            logger.warning("pdf_parse_failed", error=str(e))
            raw_text = f"[PDF解析失败: {e!s}]"

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
    tenant_id = require_tenant_id(request)
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
    status = await orchestrator.get_task_status(task_id)
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
    status = await orchestrator.get_task_status(task_id)
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
    session: AsyncSession = Depends(get_db),
):
    """Get recent interview digest history from async task records.

    Interview digests are generated asynchronously and persisted in
    ``async_tasks``; this endpoint returns the most recent completed results.
    """
    tenant_id = require_tenant_id(request)
    rows = (
        (
            await session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type == "interview_digest",
                    AsyncTask.status == "completed",
                    AsyncTask.result_json.is_not(None),
                )
                .order_by(AsyncTask.completed_at.desc().nullslast(), AsyncTask.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    digests = []
    for row in rows:
        result = None
        try:
            result = InterviewDigestResponse.model_validate_json(row.result_json or "")
        except Exception:
            result = None
        digests.append(
            {
                "task_id": row.id,
                "status": row.status,
                "progress": row.progress / 100.0 if row.progress else 0.0,
                "result": result.model_dump() if result else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {"digests": digests, "total": len(digests)}
