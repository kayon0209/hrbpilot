"""Celery tasks for scenario orchestration (interview digest, voice insight).

Dispatched from the web process; run on Celery workers. Each task updates the
``async_tasks`` table so the web process can poll progress. Durable scenario
results are also written to the scenario-specific PostgreSQL tables so history
survives restarts and can be queried without the async task row.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from datetime import UTC
from typing import Any, TypeVar

from app.shared.celery_app import celery_app
from app.shared.logger import get_logger

logger = get_logger(__name__)
T = TypeVar("T")

# Keep one event loop per Celery worker process to avoid cross-loop asyncpg issues.
_worker_loop: asyncio.AbstractEventLoop | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop


def run_async_in_worker(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the persistent event loop for this Celery child."""
    return _get_worker_loop().run_until_complete(coro)


async def _update_task(task_id: str, tenant_id: str, **fields) -> None:
    """Update AsyncTask row within the worker loop and tenant context.

    ``progress`` is only written when a real, measurable denominator exists
    (spec §9.1: mode ``units``). Scenario analysis is a single LLM generation
    with no measurable steps, so those tasks never write fake percentages.
    """
    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.get(AsyncTask, task_id)
        if not row:
            return
        for key, value in fields.items():
            if key == "status":
                row.status = value
            elif key == "progress":
                row.progress = value
            elif key == "result_json":
                row.result_json = value
            elif key == "error_message":
                row.error_message = str(value)[:2000]
            elif key == "started_at":
                row.started_at = value
            elif key == "completed_at":
                row.completed_at = value
        await db.commit()


async def expire_stale_tasks(tenant_id: str, max_age_seconds: int = 900) -> int:
    """Mark tasks stuck in pending/running as failed so nothing hangs forever.

    Spec Phase 0 exit gate: "没有无解释永久 pending". A task whose worker died
    mid-run must surface as failed with an explanatory message, not stay
    'running' until the user gives up.

    ``tenant_id`` is mandatory: ``async_tasks`` is FORCE RLS, so a session
    without the tenant context silently matches zero rows (audit 2026-08-31
    P0-1 — a document ingestion task stayed ``pending`` for 13 days because
    the previous no-context sweep could never see it).
    """
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask

    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        result = await db.execute(
            update(AsyncTask)
            .where(
                AsyncTask.tenant_id == tenant_id,
                AsyncTask.status.in_(("pending", "running")),
                AsyncTask.created_at < cutoff,
            )
            .values(
                status="failed",
                error_message="任务超时未完成，已完成的部分不会丢失，可重新发起分析。",
                completed_at=datetime.now(UTC),
            )
        )
        await db.commit()
        return int(getattr(result, "rowcount", 0) or 0)


async def _persist_interview_digest(tenant_id: str, result) -> None:
    """Persist completed interview digest result for durable history."""
    from app.data.database import get_session_factory
    from app.data.models.scenarios import InterviewDigest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = InterviewDigest(
            tenant_id=tenant_id,
            document_id=None,
            demands_json=json.dumps([item.model_dump() for item in result.employee_demands], ensure_ascii=False),
            risk_level=result.risk_level.value,
            risk_signals_json=json.dumps(result.risk_signals, ensure_ascii=False),
            action_items_json=json.dumps([item.model_dump() for item in result.action_items], ensure_ascii=False),
            suggested_owner=result.suggested_owner,
            summary=result.summary,
        )
        db.add(row)
        await db.commit()


async def _persist_voice_insight(tenant_id: str, task_id: str, result) -> None:
    """Persist completed voice insight result for durable history."""
    from app.data.database import get_session_factory
    from app.data.models.scenarios import InsightReport

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = InsightReport(
            tenant_id=tenant_id,
            task_id=task_id,
            clusters_json=json.dumps([item.model_dump() for item in result.clusters], ensure_ascii=False),
            signals_json=json.dumps([item.model_dump() for item in result.risk_signals], ensure_ascii=False),
            trends_json=json.dumps([item.model_dump() for item in result.trends], ensure_ascii=False),
        )
        db.add(row)
        await db.commit()


@celery_app.task(name="scenario.interview_digest", acks_late=True)  # type: ignore[untyped-decorator]
def interview_digest_task(task_id: str, document_content: str, tenant_id: str, user_id: str) -> None:
    """Run interview digest analysis and persist the result."""
    from datetime import datetime

    run_async_in_worker(_update_task(task_id, tenant_id, status="running", started_at=datetime.now(UTC)))

    async def _run():
        from app.scenarios.interview_digest.orchestrator import InterviewDigestOrchestrator

        orchestrator = InterviewDigestOrchestrator()
        return await orchestrator.digest(document_content, tenant_id, user_id)

    try:
        result = run_async_in_worker(_run())
        run_async_in_worker(
            _update_task(
                task_id,
                tenant_id,
                status="completed",
                result_json=result.model_dump_json(),
                completed_at=datetime.now(UTC),
            )
        )
        run_async_in_worker(_persist_interview_digest(tenant_id, result))
    except Exception as exc:
        logger.error("interview_digest_task_failed", task_id=task_id, error=str(exc))
        run_async_in_worker(
            _update_task(
                task_id,
                tenant_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
        )


@celery_app.task(name="scenario.voice_insight", acks_late=True)  # type: ignore[untyped-decorator]
def voice_insight_task(task_id: str, documents_json: str, tenant_id: str, user_id: str) -> None:
    """Run voice insight analysis and persist the result."""
    from datetime import datetime

    run_async_in_worker(_update_task(task_id, tenant_id, status="running", started_at=datetime.now(UTC)))

    async def _run():
        from app.scenarios.voice_insight.orchestrator import VoiceInsightOrchestrator

        orchestrator = VoiceInsightOrchestrator()
        documents = json.loads(documents_json)
        return await orchestrator.analyze(documents, tenant_id, user_id)

    try:
        result = run_async_in_worker(_run())
        run_async_in_worker(
            _update_task(
                task_id,
                tenant_id,
                status="completed",
                result_json=result.model_dump_json(),
                completed_at=datetime.now(UTC),
            )
        )
        run_async_in_worker(_persist_voice_insight(tenant_id, task_id, result))
    except Exception as exc:
        logger.error("voice_insight_task_failed", task_id=task_id, error=str(exc))
        run_async_in_worker(
            _update_task(
                task_id,
                tenant_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
        )
