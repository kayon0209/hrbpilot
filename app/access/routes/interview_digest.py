"""HRBP AI Workbench — Interview Digest API routes.

POST /api/interview-digest/upload → Upload interview document
POST /api/interview-digest/analyze → Start async analysis, returns task_id (+ material record)
GET  /api/interview-digest/progress/{task_id} → Poll task progress
GET  /api/interview-digest/result/{task_id} → Get completed result
GET  /api/interview-digest/history → Recent digest history (legacy contract, kept for compat)
GET  /api/interview-digest/records → Material list: summary rows, cursor pagination, search
GET  /api/interview-digest/records/{record_id} → Full record detail incl. complete result
"""

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db, get_session_factory
from app.data.models.infra import AsyncTask
from app.data.models.material import Employee, InterviewRecord
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
@require_capability("interview_digest")
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
        # structured files (Phase 6): preview sheet/header/quality before analysis
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/csv": "csv",
        "application/csv": "csv",
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

    structured_preview = None
    structured_type = None
    if allowed_types[content_type] in ("xlsx", "csv"):
        from app.rag.ingestion.structured import StructuredFileError, preview_structured, preview_to_markdown

        structured_type = allowed_types[content_type]
        try:
            structured_preview = preview_structured(content, file.filename or "未命名", structured_type)
            raw_text = preview_to_markdown(structured_preview)
        except StructuredFileError as e:
            raise ValidationError(f"{e}。修复方法：{e.repair_hint}") from e
    elif content_type == "text/plain":
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

    result: dict[str, object] = {
        "filename": file.filename,
        "content_type": allowed_types[content_type],
        "text_length": len(raw_text),
        "content": raw_text,  # In production, store in MinIO and return a reference
    }
    if structured_preview is not None:
        result["structured_preview"] = structured_preview.model_dump()
    return result


async def _get_or_create_employee(
    tenant_id: str, user_id: str, name: str, department: str | None = None
) -> Employee | None:
    """Get-or-create the employee dimension row by (tenant_id, name)."""
    cleaned = name.strip()
    if not cleaned:
        return None
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.name == cleaned,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = Employee(tenant_id=tenant_id, name=cleaned, department=department, created_by=user_id)
            db.add(row)
            await db.commit()
        return row


async def _persist_interview_record(
    tenant_id: str,
    user_id: str,
    *,
    content: str,
    task_id: str,
    employee_name: str,
    title: str,
    interview_type: str,
    interview_date: str,
) -> None:
    """Persist the raw corpus + task link. Best-effort: must not break the
    analysis pipeline if the metadata write fails."""
    try:
        employee = await _get_or_create_employee(tenant_id, user_id, employee_name)
        parsed_date = None
        if interview_date:
            try:
                parsed_date = datetime.fromisoformat(interview_date).date()
            except ValueError:
                parsed_date = None
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                InterviewRecord(
                    tenant_id=tenant_id,
                    employee_id=employee.id if employee else None,
                    employee_name=employee.name if employee else None,
                    title=title.strip() or None,
                    interview_type=interview_type.strip() or "general",
                    interview_date=parsed_date,
                    raw_text=content,
                    raw_text_length=len(content),
                    digest_task_id=task_id,
                    status="analyzing",
                    created_by=user_id,
                )
            )
            await db.commit()
    except Exception as exc:
        logger.warning("interview_record_persist_failed", task_id=task_id, error=str(exc))


@router.post("/analyze")
@require_auth
@require_capability("interview_digest")
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

    employee_name = str(body.get("employee_name") or "").strip()
    title = str(body.get("title") or "").strip()
    interview_type = str(body.get("interview_type") or "general").strip()
    interview_date = str(body.get("interview_date") or "").strip()
    await _persist_interview_record(
        tenant_id,
        user_id,
        content=content,
        task_id=task_id,
        employee_name=employee_name,
        title=title,
        interview_type=interview_type,
        interview_date=interview_date,
    )

    return {"task_id": task_id, "status": "pending"}


@router.get("/progress/{task_id}")
@require_auth
@require_capability("interview_digest")
async def get_progress(
    task_id: str,
    request: Request,
):
    """Poll async task progress."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
    if not status:
        raise NotFoundError("Task", task_id)

    # Lazy staleness sweep: a task whose worker died mid-run must surface as
    # failed with an explanation, not hang in pending/running forever.
    if status.status in ("pending", "running"):
        from app.scenarios.tasks import expire_stale_tasks

        await expire_stale_tasks(tenant_id)
        status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
        if not status:
            raise NotFoundError("Task", task_id)

    return {
        "task_id": task_id,
        "status": status.status,
        "error": status.error,
    }


@router.get("/result/{task_id}")
@require_auth
@require_capability("interview_digest")
async def get_result(
    task_id: str,
    request: Request,
):
    """Get completed interview digest result."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
    if not status:
        raise NotFoundError("Task", task_id)

    if status.status != "completed":
        raise HTTPException(status_code=425, detail=f"任务尚未完成，当前状态: {status.status}")

    return status.result


@router.get("/history")
@require_auth
@require_capability("interview_digest")
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
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    rows = (
        (
            await session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type == "interview_digest",
                    AsyncTask.status == "completed",
                    AsyncTask.result_json.is_not(None),
                    AsyncTask.created_by.in_(visible_user_ids),
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
                "result": result.model_dump() if result else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {"digests": digests, "total": len(digests)}


# --- material library endpoints (design: docs/data-scale-design-2026-09-09.md) ---
# List rows carry summary fields only (never the full result JSON); the full
# result is loaded on demand from the detail endpoint.


def _encode_cursor(created_at: datetime, record_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{record_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_iso, record_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(created_iso), record_id
    except Exception as exc:
        raise ValidationError("无效的分页游标，请清除筛选后重试") from exc


def _summarize_record(record: InterviewRecord, task: AsyncTask | None) -> dict:
    risk_level = None
    summary = ""
    confidence = None
    if task is not None and task.result_json:
        try:
            parsed = InterviewDigestResponse.model_validate_json(task.result_json)
            risk_level = parsed.risk_level.value
            summary = parsed.summary
            confidence = parsed.confidence
        except Exception:
            pass
    return {
        "record_id": record.id,
        "employee_name": record.employee_name,
        "title": record.title,
        "interview_type": record.interview_type,
        "interview_date": record.interview_date.isoformat() if record.interview_date else None,
        "status": task.status if task is not None else record.status,
        "risk_level": risk_level,
        "summary": summary,
        "confidence": confidence,
        "raw_text_length": record.raw_text_length,
        "has_result": bool(task is not None and task.result_json),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": task.completed_at.isoformat() if task is not None and task.completed_at else None,
    }


@router.get("/records")
@require_auth
@require_capability("interview_digest")
async def list_records(
    request: Request,
    q: str = "",
    limit: int = 20,
    cursor: str = "",
    session: AsyncSession = Depends(get_db),
):
    """Material list — summary rows with keyset pagination and search.

    Search `q` matches employee name, title or raw corpus (ILIKE). At the
    500-5000 record scale this is comfortably served by Postgres; full-text
    (tsvector) is a later upgrade, not needed here yet.
    """
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    limit = max(1, min(limit, 100))

    stmt = (
        select(InterviewRecord, AsyncTask)
        .outerjoin(
            AsyncTask,
            and_(
                AsyncTask.tenant_id == InterviewRecord.tenant_id,
                AsyncTask.id == InterviewRecord.digest_task_id,
            ),
        )
        .where(
            InterviewRecord.tenant_id == tenant_id,
            InterviewRecord.created_by.in_(visible_user_ids),
        )
        .order_by(InterviewRecord.created_at.desc(), InterviewRecord.id.desc())
    )
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                InterviewRecord.employee_name.ilike(like),
                InterviewRecord.title.ilike(like),
                InterviewRecord.raw_text.ilike(like),
            )
        )
    if cursor:
        last_dt, last_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                InterviewRecord.created_at < last_dt,
                and_(InterviewRecord.created_at == last_dt, InterviewRecord.id < last_id),
            )
        )

    rows = ((await session.execute(stmt.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [_summarize_record(record, task) for record, task in rows]
    next_cursor = None
    if has_more and items:
        last_created = rows[-1][0].created_at
        next_cursor = _encode_cursor(last_created, rows[-1][0].id)
    return {"records": items, "total": len(items), "next_cursor": next_cursor}


@router.get("/records/{record_id}")
@require_auth
@require_capability("interview_digest")
async def get_record_detail(
    record_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Full material detail — complete analysis result, loaded on demand."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)

    row = (
        (
            await session.execute(
                select(InterviewRecord, AsyncTask)
                .outerjoin(
                    AsyncTask,
                    and_(
                        AsyncTask.tenant_id == InterviewRecord.tenant_id,
                        AsyncTask.id == InterviewRecord.digest_task_id,
                    ),
                )
                .where(
                    InterviewRecord.tenant_id == tenant_id,
                    InterviewRecord.id == record_id,
                    InterviewRecord.created_by.in_(visible_user_ids),
                )
            )
        )
        .first()
    )
    if row is None:
        raise NotFoundError("InterviewRecord", record_id)
    record, task = row

    result = None
    if task is not None and task.result_json:
        try:
            result = InterviewDigestResponse.model_validate_json(task.result_json).model_dump()
        except Exception:
            result = None

    return {
        "record": _summarize_record(record, task),
        "raw_text": record.raw_text,
        "result": result,
    }
