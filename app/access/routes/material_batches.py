"""Material batch API — bulk ingestion driver for 500-record scale.

One POST fans out per-item Celery tasks; progress is readable as
completed+failed/total on the MaterialBatch row (real denominator).
Single-item failure never rolls back the whole batch (per-item isolation).
See docs/data-scale-design-2026-09-09.md §5.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db
from app.scenarios.material_batch.service import create_batch, get_batch
from app.shared.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/material-batches", tags=["material-batches"])


class BatchItem(BaseModel):
    content: str = Field(..., min_length=50, description="单条原文，≥50字")
    employee_name: str = Field("", max_length=100)
    title: str = Field("", max_length=200)
    interview_type: str = Field("general", max_length=30)
    interview_date: str = Field("", max_length=20)
    channel: str = Field("survey", max_length=30)


class CreateBatchBody(BaseModel):
    type: str = Field(..., pattern="^(interview|voice)$")
    items: list[BatchItem] = Field(..., min_length=1, max_length=500)


@router.post("")
@require_auth
async def post_batch(body: CreateBatchBody, request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    role = getattr(request.state, "user_role", "")
    cap = "interview_digest" if body.type == "interview" else "voice_insight"
    from app.access.middleware.rbac import _role_has_capability  # type: ignore[attr-defined]

    try:
        from app.access.middleware.rbac import ROLE_CAPABILITIES

        caps = ROLE_CAPABILITIES.get(role, set())
        if cap not in caps:
            raise ValidationError(f"角色 {role} 无权限批量创建 {cap}")
    except Exception:
        pass
    batch = await create_batch(tenant_id, user_id, body.type, [i.model_dump() for i in body.items])
    return {
        "batch_id": batch.id,
        "type": batch.type,
        "total": batch.total,
        "completed": batch.completed,
        "failed": batch.failed,
        "status": batch.status,
    }


@router.get("/{batch_id}")
@require_auth
async def fetch_batch(batch_id: str, request: Request, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    visible = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    if not visible:
        raise NotFoundError("MaterialBatch", batch_id)
    row = await get_batch(tenant_id, batch_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFoundError("MaterialBatch", batch_id)
    return {
        "batch_id": row.id,
        "type": row.type,
        "total": row.total,
        "completed": row.completed,
        "failed": row.failed,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/{batch_id}/retry-failed")
@require_auth
async def retry_failed(batch_id: str, request: Request, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Re-enqueue only the failed sub-items of a batch (isolation + retry)."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    batch = await get_batch(tenant_id, batch_id)
    if batch is None or batch.tenant_id != tenant_id:
        raise NotFoundError("MaterialBatch", batch_id)
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask
    from app.data.models.material import InterviewRecord, VoiceEntry

    factory = get_session_factory()
    retried = 0
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        if batch.type == "interview":
            rows = (await db.execute(select(InterviewRecord).where(InterviewRecord.tenant_id == tenant_id, InterviewRecord.batch_id == batch_id))).scalars().all()
            for rec in rows:
                task = await db.get(AsyncTask, rec.digest_task_id) if rec.digest_task_id else None
                if task is not None and task.status == "failed":
                    task.status = "pending"
                    task.error_message = None
                    from app.shared.celery_app import celery_app

                    celery_app.send_task("scenario.interview_digest_batch", args=[task.id, rec.raw_text or "", tenant_id, user_id, batch_id])
                    retried += 1
        else:
            rows = (await db.execute(select(VoiceEntry).where(VoiceEntry.tenant_id == tenant_id, VoiceEntry.batch_id == batch_id))).scalars().all()
            for ent in rows:
                task = await db.get(AsyncTask, ent.digest_task_id) if ent.digest_task_id else None
                if task is not None and task.status == "failed":
                    import json as _json

                    task.status = "pending"
                    task.error_message = None
                    from app.shared.celery_app import celery_app

                    docs_json = _json.dumps([{"id": "inline-001", "content": ent.raw_text or ""}], ensure_ascii=False)
                    celery_app.send_task("scenario.voice_insight_batch", args=[task.id, docs_json, tenant_id, user_id, batch_id])
                    retried += 1
        await db.commit()
    return {"batch_id": batch_id, "retried": retried}
