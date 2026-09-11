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

from app.access.middleware.decorators import require_auth
from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db
from app.scenarios.material_batch.service import create_batch, get_batch
from app.shared.errors import ForbiddenError, NotFoundError

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
    # 能力门（2026-09-10 修复）。这里原先叠了两个真实缺陷：
    #   1) `from app.access.middleware.rbac import _role_has_capability` —— 该名字
    #      在 rbac.py 里根本不存在（只有 ROLE_CAPABILITIES 与 _forbidden），
    #      于是每次请求都在函数体里抛 ImportError → 该端点恒 500，500 条批量入库
    #      这条链路实际不可用；
    #   2) 即便导入修好，紧随其后的 `try: ... raise ValidationError(...)
    #      except Exception: pass` 会把**自己刚抛出的权限拒绝**一起吞掉——
    #      能力门形同虚设，任何已登录角色都能触发 500 条面谈/声音分析（消耗 LLM 预算）。
    # 现在：导入提到模块顶层（真出问题就在启动期暴露，而不是退化成静默放行），
    # 拒绝逻辑直接抛出，不做任何兜底。
    if cap not in ROLE_CAPABILITIES.get(role, set()):
        raise ForbiddenError(message="当前角色无权批量创建材料", required_role=cap)
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
    role = getattr(request.state, "user_role", "")
    batch = await get_batch(tenant_id, batch_id)
    if batch is None or batch.tenant_id != tenant_id:
        raise NotFoundError("MaterialBatch", batch_id)
    # 与创建端点同一道能力门（2026-09-10）：重试会把失败子项重新入队 Celery，
    # 也就是重新消耗 LLM；此前这里只有 require_auth + 租户校验，任何已登录角色
    # 都能对别人的批次重复点火。
    cap = "interview_digest" if batch.type == "interview" else "voice_insight"
    if cap not in ROLE_CAPABILITIES.get(role, set()):
        raise ForbiddenError(message="当前角色无权重试材料分析", required_role=cap)
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
