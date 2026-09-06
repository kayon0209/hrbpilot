"""Runnable polling worker for durable governed-tool Outbox messages.

Run one process independently from the API so a committed Outbox row is not
dependent on an in-process background task surviving the request::

    python -m app.outbox.worker

Use ``--once`` for a bounded drain in tests or operational scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket

from sqlalchemy import select

from app.data.database import get_session_factory
from app.data.models.tenant import Tenant
from app.outbox.dispatcher import OutboxDispatcher
from app.tools.executors import GOVERNED_TOOL_EXECUTORS


def _default_worker_id() -> str:
    return f"tool-dispatcher:{socket.gethostname()}:{os.getpid()}"


async def drain_tool_dispatches(
    tenant_id: str,
    *,
    max_messages: int = 100,
    worker_id: str | None = None,
) -> list[str]:
    """Drain currently eligible messages for one tenant, bounded per pass."""
    if max_messages <= 0:
        raise ValueError("max_messages must be positive")
    dispatcher = OutboxDispatcher(
        GOVERNED_TOOL_EXECUTORS,
        worker_id=worker_id or _default_worker_id(),
    )
    states: list[str] = []
    for _ in range(max_messages):
        result = await dispatcher.dispatch_once(tenant_id)
        if result.state == "idle":
            break
        states.append(result.state)
    return states


async def _tenant_ids() -> list[str]:
    factory = get_session_factory()
    async with factory() as session:
        return list((await session.execute(select(Tenant.id).order_by(Tenant.id))).scalars().all())


async def run_worker(*, once: bool, poll_interval: float, max_messages: int) -> None:
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    worker_id = _default_worker_id()
    while True:
        for tenant_id in await _tenant_ids():
            await drain_tool_dispatches(
                tenant_id,
                max_messages=max_messages,
                worker_id=worker_id,
            )
        if once:
            return
        await asyncio.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll and dispatch governed HRBPilot tool writes")
    parser.add_argument("--once", action="store_true", help="drain one bounded pass and exit")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-messages", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(
        run_worker(
            once=args.once,
            poll_interval=args.poll_interval,
            max_messages=args.max_messages,
        )
    )


if __name__ == "__main__":
    main()
