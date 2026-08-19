"""HRBP AI Workbench — Graceful shutdown handler.

Phase 15 spec: SIGTERM → stop accepting new connections →
complete in-flight requests → close DB connections → exit.
"""

import asyncio
import signal

from app.rag.pipeline import get_background_tasks
from app.shared.logger import get_logger

logger = get_logger(__name__)

_shutting_down = False
_inflight_requests: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    """Check if the application is in shutdown mode."""
    return _shutting_down


def track_request(task: asyncio.Task) -> None:
    """Register an in-flight request for graceful shutdown tracking."""
    if not _shutting_down:
        _inflight_requests.add(task)
        task.add_done_callback(_inflight_requests.discard)


async def shutdown(
    signal_name: str,
    timeout: float = 30.0,
) -> None:
    """Handle graceful shutdown."""
    global _shutting_down
    _shutting_down = True

    logger.info("graceful_shutdown_started", signal=signal_name, inflight_count=len(_inflight_requests))

    tasks = set(_inflight_requests) | set(get_background_tasks())
    if tasks:
        try:
            _done, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                logger.warning(
                    "shutdown_timeout", pending_count=len(pending), message="Some requests did not complete in time"
                )
        except Exception as e:
            logger.error("shutdown_wait_error", error=str(e))

    try:
        from app.data.database import get_engine

        engine = get_engine()
        await engine.dispose()
        logger.info("database_connections_closed")
    except Exception as e:
        logger.error("database_close_error", error=str(e))

    logger.info(
        "graceful_shutdown_complete",
        inflight_remaining=len(_inflight_requests),
        background_tasks=len(get_background_tasks()),
    )


def register_shutdown_handlers(timeout: float = 30.0) -> None:
    """Register OS signal handlers for graceful shutdown."""
    loop = asyncio.get_event_loop()

    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            sig = getattr(signal, sig_name)
            loop.add_signal_handler(sig, lambda name=sig_name: asyncio.create_task(shutdown(name, timeout)))  # type: ignore[misc]
        except NotImplementedError:
            logger.info("signal_handler_skipped", signal=sig_name, reason="not_supported_on_windows")
