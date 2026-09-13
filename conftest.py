"""Pytest bootstrap — ensure the project root is importable as ``app``."""

import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_llm_provider_module_state():
    """Snapshot and restore the LLM orchestrator module globals per test.

    ``_init_providers()`` sets ``_ACTIVE_PROVIDER`` to the configured default
    (e.g. ``zhipu``) on first use and the module never resets it, so any test
    that merely instantiates an orchestrator leaks a non-empty active provider
    into later tests that assert on the untouched-global contract. Restoring
    the snapshot keeps test order independent without changing production
    behavior; the global itself is what v2 §13.1 will remove.
    """

    import app.rag.llm.orchestrator as _orch

    captured = (_orch._PROVIDER_REGISTRY, _orch._ACTIVE_PROVIDER)
    yield
    _orch._PROVIDER_REGISTRY, _orch._ACTIVE_PROVIDER = captured


@pytest.fixture()
async def sqlite_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine torn down inside the event loop that used it.

    aiosqlite runs every statement on a dedicated worker thread: the thread
    starts with the connection and only ends when that connection is closed.
    Disposing the engine *inside the loop that used it* is what ends the
    thread deterministically — ``await engine.dispose()`` cannot return until
    the worker has delivered its final result.

    Leaving the engine to garbage collection instead makes the worker outlive
    its loop: ``Connection.__del__`` enqueues a stop request onto whatever
    loop happens to be running, the loop closes at test teardown, and the
    worker then delivers that result through ``call_soon_threadsafe()`` on a
    closed loop. The resulting ``RuntimeError: Event loop is closed`` happens
    inside the worker thread, so pytest reports it as
    ``PytestUnhandledThreadExceptionWarning`` — which GitHub turns into red
    ``##[error]`` annotations even though every step still succeeded.
    """

    pytest.importorskip("aiosqlite", reason="aiosqlite not installed")
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        yield engine
    finally:
        await engine.dispose()


def _live_sqlite_worker_threads() -> list[threading.Thread]:
    """Live aiosqlite statement-worker threads — one per open connection."""

    return [
        thread
        for thread in threading.enumerate()
        if getattr(getattr(thread, "_target", None), "__name__", "") == "_connection_worker_thread"
    ]


@pytest.fixture(scope="session", autouse=True)
def _sqlite_connection_lifecycle_guard() -> Iterator[None]:
    """Fail the session if a test left an aiosqlite connection to the GC.

    The CI failure this guards (`RuntimeError: Event loop is closed` in an
    aiosqlite worker, surfaced as red annotations) is a race between garbage
    collection and event-loop teardown, so it reproduces on CI and not on a
    developer machine. Both counters below are checked deterministically
    instead of waiting for the race:

    1. every connection that was opened must also have been *explicitly*
       closed — a connection rescued only by ``Connection.__del__`` still
       counts as open, because that is exactly the path that races the loop;
    2. no worker thread may outlive the session.

    Use the ``sqlite_engine`` fixture (or ``await engine.dispose()`` inside an
    async fixture) rather than relying on collection.
    """

    import aiosqlite.core as aiosqlite_core

    stats = {"opened": 0, "closed": 0}
    original_init = aiosqlite_core.Connection.__init__
    original_close = aiosqlite_core.Connection.close

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        stats["opened"] += 1
        original_init(self, *args, **kwargs)

    async def counting_close(self: Any) -> None:
        if self._connection is not None:
            stats["closed"] += 1
        await original_close(self)

    aiosqlite_core.Connection.__init__ = counting_init  # type: ignore[method-assign]
    aiosqlite_core.Connection.close = counting_close  # type: ignore[method-assign]

    baseline_threads = set(_live_sqlite_worker_threads())
    try:
        yield
    finally:
        aiosqlite_core.Connection.__init__ = original_init  # type: ignore[method-assign]
        aiosqlite_core.Connection.close = original_close  # type: ignore[method-assign]

    # A worker that is merely finishing its stop handshake exits in
    # milliseconds; one that is parked on a queue nobody drains never exits.
    deadline = time.monotonic() + 5.0
    leaked_threads: list[threading.Thread] = []
    while True:
        leaked_threads = [t for t in _live_sqlite_worker_threads() if t not in baseline_threads]
        if not leaked_threads or time.monotonic() >= deadline:
            break
        time.sleep(0.05)

    hint = (
        "Dispose it inside the owning event loop — e.g. depend on the "
        "`sqlite_engine` fixture from conftest.py — instead of letting garbage "
        "collection run Connection.__del__ while an event loop is live."
    )
    assert stats["opened"] == stats["closed"], (
        f"{stats['opened'] - stats['closed']} aiosqlite connection(s) opened during this "
        f"session were never closed explicitly (opened={stats['opened']}, "
        f"closed={stats['closed']}). {hint}"
    )
    assert not leaked_threads, (
        f"{len(leaked_threads)} aiosqlite worker thread(s) outlived the session: "
        f"{sorted(t.name for t in leaked_threads)}. {hint}"
    )
