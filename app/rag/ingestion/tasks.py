"""Celery entry points for the asynchronous ingestion pipeline."""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from app.rag.ingestion.pipeline import run_ingestion_task
from app.shared.celery_app import celery_app

T = TypeVar("T")

# Celery's default prefork pool invokes the same task function multiple times in
# each child process.  Calling ``asyncio.run`` per invocation creates a new
# event loop, while SQLAlchemy/asyncpg keeps pooled connections bound to the
# first one.  Subsequent ingestion jobs then fail with "Future attached to a
# different loop".  A dedicated loop per Celery child keeps the async database
# pool and all task executions on the same loop.
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


@celery_app.task(name="rag.ingest", acks_late=True)  # type: ignore[untyped-decorator]
def ingest_task(task_id: str, tenant_id: str) -> None:
    run_async_in_worker(run_ingestion_task(task_id, tenant_id))


def dispatch_ingestion_task(task_id: str, tenant_id: str) -> None:
    celery_app.send_task("rag.ingest", args=[task_id, tenant_id])
