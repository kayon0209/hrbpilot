"""Health/readiness contract tests (PR-01).

Locks the observability contract revised in the PR-01 review:

  - ``/api/health`` is LIFENESS ONLY: the payload never carries dependency
    checks, per-service latency, or internal topology.
  - ``/api/ready`` is READINESS ONLY: it reports dependency reachability and
    never leaks host names, ports, or driver errors.
  - Segmented latency is emitted via structured logs (pipeline_segment_latency
    / policy_qa_segment_latency), NOT stuffed into the health response.

Revised 2026-09-11 — readiness is **graded**, not a single boolean:

  - every check declares a ``tier`` (``critical`` | ``optional``);
  - a failing optional dependency is ``unavailable``, never ``error``;
  - critical failure ⇒ ``status: not_ready`` + **HTTP 503**, so the endpoint can
    actually be used as a Kubernetes readinessProbe instead of reporting
    "degraded" forever on deployments that skip Milvus/MinIO;
  - optional-only failure ⇒ ``status: degraded`` + HTTP 200 (still serving).

These tests stub every dependency check so they are hermetic, fast, and
deterministic without live PostgreSQL/Redis/Milvus/MinIO.
"""

import asyncio
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
    """/api/ready reports reachability; each check declares status + tier."""
    with _client() as client:
        resp = client.get("/api/ready")
    # Probes answer before auth; 503 is a legal readiness verdict, 401 is not.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] in ("ok", "degraded", "not_ready")
    checks = body["checks"]
    # Present keys are the known dependency set; anything else would leak
    # internal wiring that does not belong in a public readiness payload.
    allowed = {"database", "redis", "milvus", "minio", "embedding"}
    assert set(checks).issubset(allowed)
    for _name, state in checks.items():
        # Public payload carries status + tier only — no host, port or
        # driver text, ever.
        assert set(state) == {"status", "tier"}
        assert state["tier"] in ("critical", "optional")
        assert state["status"] in ("ok", "unavailable", "error")


def test_ready_not_ready_with_all_deps_down(deps_down):
    """Every dependency down ⇒ not_ready, and the tier split is still visible.

    The pre-2026-09-11 bug: this returned a flat ``degraded`` with all five
    checks marked ``error``, so nothing distinguished "cannot serve" from
    "a capability is missing".
    """
    with _client() as client:
        resp = client.get("/api/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["critical_failed"] == ["database", "redis"]
    assert body["optional_unavailable"] == ["embedding", "milvus", "minio"]
    # Severity wording maps onto the tier: critical=error, optional=unavailable.
    for name in body["critical_failed"]:
        assert body["checks"][name] == {"status": "error", "tier": "critical"}
    for name in body["optional_unavailable"]:
        assert body["checks"][name] == {"status": "unavailable", "tier": "optional"}


def test_ready_degraded_when_only_optional_deps_are_down(monkeypatch):
    """Optional-only failure ⇒ still serving (200 + degraded), not 503.

    This is the deployment shape that used to be reported as broken: Milvus /
    MinIO intentionally not deployed, PostgreSQL and Redis healthy.
    """
    from app.config import settings as settings_module

    # Start from a fully healthy stub set, then break only the optional side
    # (later monkeypatch.setattr calls override earlier ones).
    _patch_healthy(monkeypatch)
    monkeypatch.setattr("app.rag.storage.milvus.MilvusStore", _FakeUnavailable)
    monkeypatch.setattr("app.rag.storage.object_store.ObjectStore", _FakeUnavailable)
    monkeypatch.setattr(settings_module.settings, "embedding_base_url", "")

    with _client() as client:
        resp = client.get("/api/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["critical_failed"] == []
    assert body["optional_unavailable"] == ["embedding", "milvus", "minio"]


def test_ready_ok_when_everything_is_reachable(monkeypatch):
    _patch_healthy(monkeypatch)
    with _client() as client:
        resp = client.get("/api/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["critical_failed"] == []
    assert body["optional_unavailable"] == []


def _patch_healthy(monkeypatch):
    """Stub every dependency probe as reachable (no live infra)."""

    class _FakeConn:
        async def execute(self, _sql):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    class _FakeRedisClient:
        async def ping(self):
            return True

        async def close(self):
            return None

    class _FakeRedis:
        @staticmethod
        def from_url(_url):
            return _FakeRedisClient()

    class _FakeAvailable:
        async def check_connection_async(self):
            return True

    from app.config import settings as settings_module

    monkeypatch.setattr("app.data.database.get_engine", lambda: _FakeEngine())
    monkeypatch.setattr("redis.asyncio.from_url", _FakeRedis.from_url)
    monkeypatch.setattr("app.rag.storage.milvus.MilvusStore", _FakeAvailable)
    monkeypatch.setattr("app.rag.storage.object_store.ObjectStore", _FakeAvailable)
    monkeypatch.setattr(settings_module.settings, "embedding_base_url", "https://emb.example.com/v1")
    monkeypatch.setattr(settings_module.settings, "embedding_api_key", "test-key")


def test_ready_check_is_time_boxed(monkeypatch):
    """A hung dependency must not hang the probe (K8s default timeout is 1s).

    Real evidence: the MinIO client retries for ~17s against a dead endpoint,
    which alone would blow past the probe budget.
    """
    from app.access.routes import health as health_module

    async def _hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(health_module, "CHECK_TIMEOUT_SECONDS", 0.05)
    result = asyncio.run(health_module._run_check("hang", "critical", _hang))
    assert result == {"status": "error", "tier": "critical"}


def test_readiness_never_leaks_topology(monkeypatch):
    """Driver text, hosts and ports belong in logs, never in the payload."""
    _patch_healthy(monkeypatch)
    with _client() as client:
        raw = client.get("/api/ready").text
    for leaked in ("localhost", "19530", "9000", "5432", "6379", "Traceback", "unreachable fake"):
        assert leaked not in raw


def test_ready_never_contains_segment_timing():
    with _client() as client:
        body = client.get("/api/ready").json()
    assert "latency_ms" not in body
    assert "segments_ms" not in body
    assert all("latency" not in c for c in body["checks"].values())


def test_health_and_ready_are_public_unauthenticated():
    """Probes must answer before auth so orchestrators can restart us.

    Readiness legitimately returns 503 when a critical dependency is down —
    what matters here is that it is answered, not redirected to a login.
    """
    with _client() as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code in (200, 503)


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
