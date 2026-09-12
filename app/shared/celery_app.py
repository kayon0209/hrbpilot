"""Durable background-task transport for ingestion and other long jobs.

Queue topology (batch C): ingestion (parse/embed/write) and scenario
analysis (LLM bulk) are routed to SEPARATE queues so a heavy document
rebuild can never starve interactive scenario tasks, and each queue can
be scaled independently (--concurrency per -Q worker).
"""

from celery import Celery  # type: ignore[import-untyped]

from app.config.settings import settings

celery_app = Celery(
    "hrbpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend_url,
    include=["app.rag.ingestion.tasks", "app.scenarios.tasks"],
)

# Queue names — workers bind with -Q; task_routes maps task name → queue.
QUEUE_INGEST = "kb_ingest"
QUEUE_SCENARIO = "scenario"

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Hard ceiling per task: a lost worker must surface as a failed task,
    # never a task stuck in pending/running forever (spec Phase 0).
    task_soft_time_limit=600,
    task_time_limit=660,
    task_routes={
        "rag.ingest": {"queue": QUEUE_INGEST},
        "scenario.*": {"queue": QUEUE_SCENARIO},
    },
)

# Backpressure: refuse dispatch when a queue is already deep. Without this a
# bulk upload of hundreds of documents would pile messages faster than the
# single worker drains them, hiding hours of latency behind "accepted".
QUEUE_DEPTH_LIMIT = 200


def queue_depth(queue: str) -> int | None:
    """Return the pending message count for ``queue``, or None if the broker
    cannot be reached (backpressure then degrades open, not hard-fail)."""
    try:
        from redis import Redis

        r = Redis.from_url(settings.celery_broker_url, decode_responses=True)
        try:
            depth = r.llen(queue)
            # sync client returns int; the Awaitable half of the stub union
            # only applies to the async client variant.
            assert isinstance(depth, int)
            return depth
        finally:
            r.close()
    except Exception:
        return None


def check_backpressure(queue: str) -> None:
    """Raise ValidationError when the queue is over its depth limit."""
    from app.shared.errors import ValidationError

    depth = queue_depth(queue)
    if depth is not None and depth >= QUEUE_DEPTH_LIMIT:
        raise ValidationError(
            f"后台队列 {queue} 已积压 {depth} 个任务（上限 {QUEUE_DEPTH_LIMIT}），请稍后重试或增加 worker"
        )
