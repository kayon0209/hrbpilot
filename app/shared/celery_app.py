"""Durable background-task transport for ingestion and other long jobs."""

from celery import Celery  # type: ignore[import-untyped]

from app.config.settings import settings

celery_app = Celery(
    "hrbpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend_url,
    include=["app.rag.ingestion.tasks", "app.scenarios.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Hard ceiling per task: a lost worker must surface as a failed task,
    # never a task stuck in pending/running forever (spec Phase 0).
    task_soft_time_limit=600,
    task_time_limit=660,
)
