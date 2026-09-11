"""
Material batch service — 500-record scale bulk ingestion driver.

One API call creates a MaterialBatch row and fans out per-item Celery tasks.
Each sub-item shares the batch_id so:
- progress is measurable as completed+failed / total (stage words + real denominator)
- a single LLM failure only marks that row failed, bulk never re-runs wholesale
- the batch status flips to completed once every sub-task settles
This design directly addresses the doc §5 bottleneck (solo pool 1.5h for 500 LLM calls):
fanout lets a scaled concurrency (>1) cut wall time, but the driver itself does not
require concurrency to function correctly on the default solo worker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask
from app.data.models.material import MaterialBatch
from app.shared.logger import get_logger

logger = get_logger(__name__)


async def create_batch(
    tenant_id: str, user_id: str, batch_type: str, items: list[dict]
) -> MaterialBatch:
    """Create a batch and enqueue per-item Celery tasks."""
    from app.shared.celery_app import celery_app

    total = len(items)
    batch_id = str(uuid.uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        batch = MaterialBatch(id=batch_id, tenant_id=tenant_id, type=batch_type, total=total, created_by=user_id)
        db.add(batch)
        await db.commit()
        await db.refresh(batch)

    for item in items:
        task_id = str(uuid.uuid4())
        raw = str(item.get("content") or item.get("raw_text") or "").strip()
        employee_name = str(item.get("employee_name") or "").strip()
        title = str(item.get("title") or "").strip()
        interview_type = str(item.get("interview_type") or "general").strip()
        interview_date = str(item.get("interview_date") or "").strip()
        channel = str(item.get("channel") or "survey").strip()

        # pre-create task row so progress is visible even before worker picks it up
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                AsyncTask(
                    id=task_id,
                    tenant_id=tenant_id,
                    created_by=user_id,
                    type=("interview_digest" if batch_type == "interview" else "voice_insight"),
                    status="pending",
                )
            )
            await db.commit()

        # persist material row linking the batch and the task (task not yet run)
        try:
            from app.data.models.material import Employee, InterviewRecord, VoiceEntry

            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                employee = None
                if employee_name:
                    row = (
                        await db.execute(
                            select(Employee).where(
                                Employee.tenant_id == tenant_id, Employee.name == employee_name
                            )
                        )
                    ).scalar_one_or_none()
                    if row is None and employee_name:
                        row = Employee(tenant_id=tenant_id, name=employee_name, created_by=user_id)
                        db.add(row)
                        await db.commit()
                        await db.refresh(row)
                    employee = row
                parsed_date = None
                if interview_date:
                    try:
                        parsed_date = datetime.fromisoformat(interview_date).date()
                    except ValueError:
                        parsed_date = None
                if batch_type == "interview":
                    db.add(
                        InterviewRecord(
                            tenant_id=tenant_id,
                            employee_id=employee.id if employee else None,
                            employee_name=employee.name if employee else None,
                            title=title or None,
                            interview_type=interview_type or "general",
                            interview_date=parsed_date,
                            raw_text=raw,
                            raw_text_length=len(raw),
                            digest_task_id=task_id,
                            batch_id=batch_id,
                            status="analyzing",
                            created_by=user_id,
                        )
                    )
                else:
                    db.add(
                        VoiceEntry(
                            tenant_id=tenant_id,
                            employee_id=employee.id if employee else None,
                            employee_name=employee.name if employee else None,
                            channel=channel or "survey",
                            raw_text=raw,
                            raw_text_length=len(raw),
                            digest_task_id=task_id,
                            batch_id=batch_id,
                            status="analyzing",
                            created_by=user_id,
                        )
                    )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — 记录关联失败必须可见（见下）
            # 这里失败会让 InterviewRecord/VoiceEntry 与 batch_id 脱钩：批次详情页
            # 看不到已入队的子项，「已完成/总数」分母也会失真。原先静默 pass。
            logger.error(
                "material_batch_record_link_failed",
                batch_id=batch_id,
                batch_type=batch_type,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )

        if batch_type == "interview":
            celery_app.send_task(
                "scenario.interview_digest_batch",
                args=[task_id, raw, tenant_id, user_id, batch_id],
            )
        else:
            import json as _json

            docs_json = _json.dumps([{"id": "inline-001", "content": raw}], ensure_ascii=False)
            celery_app.send_task(
                "scenario.voice_insight_batch",
                args=[task_id, docs_json, tenant_id, user_id, batch_id],
            )
    return batch


async def get_batch(tenant_id: str, batch_id: str) -> MaterialBatch | None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        return await db.get(MaterialBatch, batch_id)
