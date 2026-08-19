"""Celery tasks for scenario orchestration (interview digest, voice insight).

Dispatched from the web process; run on Celery workers. Each task updates the
``async_tasks`` table so the web process can poll progress. Durable scenario
results are also written to the scenario-specific PostgreSQL tables so history
survives restarts and can be queried without the async task row.
"""

import json
import uuid

from app.shared.celery_app import celery_app
from app.shared.logger import get_logger

logger = get_logger(__name__)


def _update_task(task_id: str, **fields) -> None:
    """Sync helper — update AsyncTask row. Runs in Celery worker."""
    from datetime import datetime, timezone

    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask

    factory = get_session_factory()
    import asyncio

    async def _do():
        async with factory() as db:
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

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
    finally:
        loop.close()


async def _persist_interview_digest(tenant_id: str, result) -> None:
    """Persist completed interview digest result for durable history."""
    from app.data.database import get_session_factory
    from app.data.models.scenarios import InterviewDigest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = InterviewDigest(
            tenant_id=tenant_id,
            document_id=str(uuid.uuid4()),
            demands_json=json.dumps([item.model_dump() for item in result.employee_demands], ensure_ascii=False),
            risk_level=result.risk_level.value,
            risk_signals_json=json.dumps(result.risk_signals, ensure_ascii=False),
            action_items_json=json.dumps([item.model_dump() for item in result.action_items], ensure_ascii=False),
            suggested_owner=result.suggested_owner,
            summary=result.summary,
        )
        db.add(row)
        await db.commit()


async def _persist_voice_insight(task_id: str, tenant_id: str, result) -> None:
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
    import asyncio
    from datetime import datetime, timezone

    _update_task(task_id, status="running", progress=30, started_at=datetime.now(timezone.utc))

    async def _run():
        from app.scenarios.interview_digest.orchestrator import InterviewDigestOrchestrator

        orchestrator = InterviewDigestOrchestrator()
        result = await orchestrator.digest(document_content, tenant_id, user_id)
        return result

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        _update_task(
            task_id,
            status="completed",
            progress=100,
            result_json=result.model_dump_json(),
            completed_at=datetime.now(timezone.utc),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_persist_interview_digest(tenant_id, result))
        finally:
            loop.close()
    except Exception as exc:
        logger.error("interview_digest_task_failed", task_id=task_id, error=str(exc))
        _update_task(
            task_id,
            status="failed",
            error_message=str(exc),
            completed_at=datetime.now(timezone.utc),
        )


@celery_app.task(name="scenario.voice_insight", acks_late=True)  # type: ignore[untyped-decorator]
def voice_insight_task(task_id: str, documents_json: str, tenant_id: str, user_id: str) -> None:
    """Run voice insight analysis and persist the result."""
    import asyncio
    from datetime import datetime, timezone

    _update_task(task_id, status="running", progress=30, started_at=datetime.now(timezone.utc))

    async def _run():
        from app.scenarios.voice_insight.orchestrator import VoiceInsightOrchestrator

        orchestrator = VoiceInsightOrchestrator()
        documents = json.loads(documents_json)
        result = await orchestrator.analyze(documents, tenant_id, user_id)
        return result

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        _update_task(
            task_id,
            status="completed",
            progress=100,
            result_json=result.model_dump_json(),
            completed_at=datetime.now(timezone.utc),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_persist_voice_insight(task_id, tenant_id, result))
        finally:
            loop.close()
    except Exception as exc:
        logger.error("voice_insight_task_failed", task_id=task_id, error=str(exc))
        _update_task(
            task_id,
            status="failed",
            error_message=str(exc),
            completed_at=datetime.now(timezone.utc),
        )
