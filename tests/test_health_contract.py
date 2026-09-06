"""Health/readiness contract tests (PR-01).

Locks the observability contract revised in the PR-01 review:

  - ``/api/health`` is LIFENESS ONLY: the payload never carries dependency
    checks, per-service latency, or internal topology.
  - ``/api/ready`` is READINESS ONLY: it reports dependency reachability as a
    boolean-ish status and never leaks host names, ports, or driver errors.
  - Segmented latency is emitted via structured logs (pipeline_segment_latency
    / policy_qa_segment_latency), NOT stuffed into the health response.

These tests stub every dependency check so they are hermetic, fast, and
deterministic without live PostgreSQL/Redis/Milvus/MinIO.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class _FakeUnavailable:
    """Stub that raises like an unreachable dependency."""

    async def check_connection_async(self):
        raise RuntimeError("unreachable fake dependency")


def _client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def deps_down(monkeypatch):
    """Force every readiness dependency into its error branch.

    Autouse so every contract test is hermetic and never touches a live
    PostgreSQL/Redis/Milvus/MinIO even inside TestClient startup.
    """

    def _engine_unreachable():
        raise RuntimeError("db unreachable")

    class _FakeRedisClient:
        async def ping(self):
            raise ConnectionError("redis unreachable")

        async def close(self):
            return None

    class _FakeRedis:
        @staticmethod
        def from_url(_url):
            return _FakeRedisClient()

    monkeypatch.setattr("app.data.database.get_engine", _engine_unreachable)
    monkeypatch.setattr("redis.asyncio.from_url", _FakeRedis.from_url)
    monkeypatch.setattr("app.rag.storage.milvus.MilvusStore", _FakeUnavailable)
    monkeypatch.setattr("app.rag.storage.object_store.ObjectStore", _FakeUnavailable)
    # Embedding is configured by env; force it down so every check is error.
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "embedding_base_url", "")


def test_health_is_liveness_only():
    """/api/health must not grow into a dependency dashboard."""
    with _client() as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Liveness payload stays minimal: no dependency checks, no segment timing.
    assert "checks" not in body
    assert "database" not in body
    assert "latency" not in body
    assert "segments_ms" not in body


def test_ready_reports_dependency_status():
    """/api/ready reports reachability; each check is a flat status object."""
    with _client() as client:
        resp = client.get("/api/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    checks = body["checks"]
    # Present keys are the known dependency set; anything else would leak
    # internal wiring that does not belong in a public readiness payload.
    allowed = {"database", "redis", "milvus", "minio", "embedding"}
    assert set(checks).issubset(allowed)
    for _name, state in checks.items():
        # Public payload carries status only — no host, port or driver text.
        assert set(state) == {"status"}
        assert state["status"] in ("ok", "error")


def test_ready_degraded_with_all_deps_down(deps_down):
    with _client() as client:
        body = client.get("/api/ready").json()
    assert body["status"] == "degraded"
    for _name, state in body["checks"].items():
        assert state == {"status": "error"}


def test_ready_never_contains_segment_timing():
    with _client() as client:
        body = client.get("/api/ready").json()
    assert "latency_ms" not in body
    assert "segments_ms" not in body
    assert all("latency" not in c for c in body["checks"].values())


def test_health_and_ready_are_public_unauthenticated():
    """Probes must answer before auth so orchestrators can restart us."""
    with _client() as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code == 200


def test_segment_latency_log_shape_is_metadata_only():
    """Structured segment logs record stage→ms maps (and counts), never
    question text, document content, or employee data (review #7)."""
    from app.rag.pipeline import SegmentTimer

    timer = SegmentTimer()
    timer.start("retrieval")
    timer.stop("retrieval")
    timer.start("llm")
    timer.stop("llm")
    meta = timer.as_metadata()
    assert set(meta) == {"retrieval", "llm"}
    for value in meta.values():
        assert isinstance(value, int) and value >= 0
    # Sanity: no free-form text fields can hide content in this structure.
    assert all(isinstance(v, int) for v in meta.values())


def test_health_payload_has_no_query_or_employee_fields():
    with _client() as client:
        body = client.get("/api/health").json()
    raw = json.dumps(body, ensure_ascii=False)
    assert "question" not in raw
    assert "employee" not in raw
    assert "content" not in raw
