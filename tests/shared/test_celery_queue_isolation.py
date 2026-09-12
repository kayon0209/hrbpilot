"""Celery queue isolation and backpressure tests (batch C).

Locks:
  - task_routes map ingest → kb_ingest and every scenario.* → scenario
  - dispatch_ingestion_task sends to the ingest queue with the backpressure
    gate applied first
  - check_backpressure raises on a deep queue and degrades OPEN when the
    broker is unreachable (never blocks work on a monitoring failure)
"""

from unittest.mock import patch

import pytest

from app.rag.ingestion.tasks import dispatch_ingestion_task
from app.shared.celery_app import (
    QUEUE_DEPTH_LIMIT,
    QUEUE_INGEST,
    QUEUE_SCENARIO,
    celery_app,
    check_backpressure,
    dispatch_task,
    ensure_capacity,
    queue_depth,
    remaining_capacity,
)
from app.shared.errors import ValidationError


def test_task_routes_isolate_ingest_from_scenarios():
    routes = celery_app.conf.task_routes
    assert routes["rag.ingest"]["queue"] == QUEUE_INGEST
    assert routes["scenario.*"]["queue"] == QUEUE_SCENARIO


def test_queue_names_are_distinct():
    assert QUEUE_INGEST != QUEUE_SCENARIO
    assert QUEUE_INGEST == "kb_ingest"
    assert QUEUE_SCENARIO == "scenario"


def test_check_backpressure_raises_on_deep_queue(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: QUEUE_DEPTH_LIMIT + 5)
    with pytest.raises(ValidationError, match="积压"):
        check_backpressure(QUEUE_INGEST)


def test_check_backpressure_passes_under_limit(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: 3)
    check_backpressure(QUEUE_INGEST)  # no raise


def test_backpressure_degrades_open_when_broker_unreachable(monkeypatch):
    """Broker probe failure must not block dispatch — backpressure is a
    quality gate, not a hard dependency."""
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: None)
    check_backpressure(QUEUE_INGEST)  # no raise


def test_queue_depth_returns_none_on_broker_error():
    with patch("redis.Redis.from_url", side_effect=ConnectionError("no broker")):
        assert queue_depth(QUEUE_INGEST) is None


def test_dispatch_ingestion_task_checks_backpressure_then_routes(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: 0)

    def fake_send(task_name, args=None, queue=None):
        calls.append((task_name, {"args": args, "queue": queue}))

    monkeypatch.setattr(celery_app, "send_task", fake_send)
    dispatch_ingestion_task("task-1", "t1")
    assert calls == [("rag.ingest", {"args": ["task-1", "t1"], "queue": QUEUE_INGEST})]


def test_dispatch_ingestion_task_blocked_when_queue_deep(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: QUEUE_DEPTH_LIMIT + 1)

    def fail_send(*a, **kw):
        raise AssertionError("send_task must not run under backpressure")

    monkeypatch.setattr(celery_app, "send_task", fail_send)
    with pytest.raises(ValidationError):
        dispatch_ingestion_task("task-1", "t1")


# --- single dispatch entry point (batch dispatch must not bypass backpressure) ---


def test_dispatch_task_routes_to_the_requested_queue(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: 0)
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, args=None, queue=None: calls.append((name, queue)),
    )
    dispatch_task("scenario.voice_insight_batch", ["a"], QUEUE_SCENARIO)
    assert calls == [("scenario.voice_insight_batch", QUEUE_SCENARIO)]


def test_dispatch_task_blocks_when_queue_is_deep(monkeypatch):
    """A direct send_task call would silently bypass the ceiling; dispatch_task
    is the only entry point so this cannot be forgotten by a new caller."""
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: QUEUE_DEPTH_LIMIT + 1)

    def fail_send(*a, **kw):
        raise AssertionError("send_task must not run under backpressure")

    monkeypatch.setattr(celery_app, "send_task", fail_send)
    with pytest.raises(ValidationError, match="积压"):
        dispatch_task("scenario.voice_insight_batch", ["a"], QUEUE_SCENARIO)


def test_remaining_capacity_reflects_free_slots(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: QUEUE_DEPTH_LIMIT - 7)
    assert remaining_capacity(QUEUE_SCENARIO) == 7


def test_remaining_capacity_is_none_when_broker_unknown(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: None)
    assert remaining_capacity(QUEUE_SCENARIO) is None


def test_ensure_capacity_rejects_oversized_batch_up_front(monkeypatch):
    """500 items into a queue with 5 free slots must be refused as a whole —
    not half-dispatched and then failed."""
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: QUEUE_DEPTH_LIMIT - 5)
    with pytest.raises(ValidationError, match="剩余容量"):
        ensure_capacity(QUEUE_SCENARIO, 500)


def test_ensure_capacity_allows_a_batch_that_fits(monkeypatch):
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: 0)
    ensure_capacity(QUEUE_SCENARIO, QUEUE_DEPTH_LIMIT - 1)  # no raise


def test_ensure_capacity_degrades_open_when_broker_unreachable(monkeypatch):
    """Backpressure is a quality gate, not a dependency: a monitoring probe
    failure must not block creating a batch."""
    monkeypatch.setattr("app.shared.celery_app.queue_depth", lambda q: None)
    ensure_capacity(QUEUE_SCENARIO, 10_000)  # no raise
