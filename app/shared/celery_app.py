"""Durable background-task transport for ingestion and other long jobs.

Queue topology (batch C): ingestion (parse/embed/write) and scenario
analysis (LLM bulk) are routed to SEPARATE queues so a heavy document
rebuild can never starve interactive scenario tasks, and each queue can
be scaled independently (--concurrency per -Q worker).
"""

from typing import Any

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


def remaining_capacity(queue: str) -> int | None:
    """Free slots before ``queue`` reaches its ceiling (None when unknown).

    Lets a batch caller reject the whole batch UP FRONT instead of dispatching
    half of it and then failing — a partial dispatch leaves rows marked
    "pending" that no worker will ever pick up, which is a silently broken
    batch rather than a loud rejection.
    """
    depth = queue_depth(queue)
    if depth is None:
        return None
    return max(0, QUEUE_DEPTH_LIMIT - depth)


def ensure_capacity(queue: str, count: int) -> None:
    """Reject a batch UP FRONT when ``count`` items would not fit.

    Batch dispatch checks this once, before enqueueing anything: dispatching
    half a batch and then failing leaves rows marked "pending" that no worker
    will ever pick up, which is a silently broken batch rather than a loud
    rejection.
    """
    from app.shared.errors import ValidationError

    capacity = remaining_capacity(queue)
    if capacity is not None and count > capacity:
        raise ValidationError(f"批量 {count} 条超过队列 {queue} 的剩余容量 {capacity}，请分批处理或增加 worker")


def dispatch_task(name: str, args: list[Any], queue: str) -> None:
    """The single dispatch entry point: backpressure first, then send.

    Every dispatch must go through here. Calling ``celery_app.send_task``
    directly silently bypasses the depth ceiling — that is exactly how a
    500-record batch floods the queue the backpressure exists to protect.
    """
    check_backpressure(queue)
    celery_app.send_task(name, args=args, queue=queue)
