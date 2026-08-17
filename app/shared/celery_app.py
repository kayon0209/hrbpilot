"""Durable background-task transport for ingestion and other long jobs."""

from celery import Celery  # type: ignore[import-untyped]

from app.config.settings import settings

celery_app = Celery(
    "hrbpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend_url,
    include=["app.rag.ingestion.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)
