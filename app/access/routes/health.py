"""HRBP AI Workbench — health check and readiness endpoints.

/health → liveness (is the process alive?)
/ready  → readiness (can THIS instance serve traffic right now?)

Readiness semantics (revised 2026-09-11)
----------------------------------------
The previous implementation OR-ed every dependency into one ``ok``/``degraded``
flag, giving Milvus/MinIO the same weight as PostgreSQL/Redis.  Consequence:
on any deployment that intentionally omits an optional dependency the endpoint
is **permanently** ``degraded`` — which makes it unusable as a Kubernetes
``readinessProbe`` (the pod would never be marked ready) and, worse, trains
operators to ignore it.

Dependencies are therefore split by **blast radius**:

* **critical** — losing one means the instance cannot serve *any* request.
  → overall ``not_ready`` + **HTTP 503** (so orchestrators actually act).
  - ``database``: every authenticated route reads it.
  - ``redis``: the rate limiter is **fail-closed** — Redis down ⇒ HTTP 429 for
    every authenticated request. Measured, not assumed:
    ``docs/operations/dependency-failure-matrix.md``.
* **optional** — losing one disables a specific capability, not the service.
  → overall ``degraded`` + HTTP 200.
  - ``milvus``: dense retrieval (hybrid falls back to sparse — see
    ``Retriever._hybrid``).
  - ``minio``: object storage for uploads/attachments.
  - ``embedding``: dense vectors + rerank (sparse/FTS retrieval still works).
  - ``llm``: **observed** provider health, not a probe — see below.

Why ``llm`` is reported but not probed (added 2026-09-12)
--------------------------------------------------------
A provider can be failing on every real request while every other check is
green: a 402 "insufficient balance" from the preferred provider still produces
answers, because the request-level fallback silently serves them from the next
provider. Measured on this machine — DeepSeek 402 on every policy-QA request,
``/api/ready`` reporting ``status: ok`` throughout.

There is no honest cheap probe (a real call costs tokens, and probing a
different code path than the one serving traffic can be green while real
requests fail), so ``llm`` reports **what actually happened**: providers whose
last call raised and which have not succeeded since, within a 5-minute window
(``app.rag.llm.provider_health``). Consequences:

* a healthy instance that has served no LLM traffic yet reports ``ok`` —
  we do not invent a failure out of zero observations;
* a primary-provider failure that is masked by a successful fallback still
  shows up as ``unavailable`` ⇒ ``degraded``;
* the failing provider *names* go to structured logs only. The readiness
  payload is public, and the discipline below (no hosts, no ports, no vendor
  topology) is unchanged.

Per-check status is intentionally three-valued so a reader never has to
re-derive severity from the tier:

* ``ok``            reachable
* ``unavailable``   optional dependency not reachable → capability degraded
* ``error``         critical dependency not reachable → instance cannot serve

Public payload discipline (audit 2026-08-31 P2-1) is unchanged: no host names,
no ports, no driver text.  Raw exception text goes to the server log only.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

# A probe that hangs is as useless as a probe that lies: the MinIO client
# retries for ~17s against a dead endpoint, blowing past the 1s default
# Kubernetes probe timeout.  Cap every check.
CHECK_TIMEOUT_SECONDS = 5.0

CRITICAL = "critical"
OPTIONAL = "optional"

# Which providers were degraded the last time readiness reported them — used to
# log transitions only, not every probe tick (see readiness_check).
_last_degraded_llm: list[str] = []


@router.get("/health")
async def health_check():
    """Liveness probe — always returns ok if the process is alive."""
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


async def _check_database() -> None:
    from sqlalchemy import text

    from app.data.database import get_engine

    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url)
    try:
        await r.ping()
    finally:
        await r.close()


async def _check_milvus() -> None:
    from app.rag.storage.milvus import MilvusStore

    await MilvusStore().check_connection_async()


async def _check_minio() -> None:
    from app.rag.storage.object_store import ObjectStore

    await ObjectStore().check_connection_async()


async def _run_check(name: str, tier: str, probe) -> dict[str, str]:
    """Run one dependency probe and map the outcome onto the 3-valued status.

    Timeouts and connection errors are treated identically: from the caller's
    point of view ``too slow`` and ``down`` are the same answer — this instance
    cannot rely on that dependency right now.
    """
    try:
        await asyncio.wait_for(probe(), timeout=CHECK_TIMEOUT_SECONDS)
        return {"status": "ok", "tier": tier}
    except asyncio.TimeoutError:
        logger.warning(
            "readiness_check_timeout",
            dependency=name,
            tier=tier,
            timeout_seconds=CHECK_TIMEOUT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - a probe must never raise
        # Critical failures are operationally actionable; optional ones are
        # capacity/capability signals. Log severity follows the tier.
        log = logger.error if tier == CRITICAL else logger.warning
        log(f"{name}_unavailable", tier=tier, error=str(e))
    return {"status": "error" if tier == CRITICAL else "unavailable", "tier": tier}


@router.get("/ready")
async def readiness_check():
    global _last_degraded_llm


    """Readiness probe — can this instance serve traffic?

    HTTP status carries the verdict so orchestrators can act on it directly:

    * ``200`` + ``status: ok``        — everything reachable
    * ``200`` + ``status: degraded``  — serving, but a capability is degraded
    * ``503`` + ``status: not_ready`` — a critical dependency is down

    The payload is PUBLIC (no auth) and never discloses internal topology.
    """
    checks = {
        "database": await _run_check("database", CRITICAL, _check_database),
        "redis": await _run_check("redis", CRITICAL, _check_redis),
        "milvus": await _run_check("milvus", OPTIONAL, _check_milvus),
        "minio": await _run_check("minio", OPTIONAL, _check_minio),
    }

    # Embedding is a configuration fact rather than a reachability probe: there
    # is no cheap "is the vendor up" call that does not spend tokens.
    embedding_configured = bool(settings.embedding_base_url and settings.effective_embedding_api_key)
    if not embedding_configured:
        logger.warning("embedding_unconfigured", detail="missing endpoint or API key")
    checks["embedding"] = {
        "status": "ok" if embedding_configured else "unavailable",
        "tier": OPTIONAL,
    }

    # LLM health is OBSERVED, not probed: providers whose last real call raised
    # and which have not succeeded since. See the module docstring. The payload
    # carries status + tier only; which providers failed goes to the log.
    from app.rag.llm.provider_health import degraded_providers

    degraded = degraded_providers()
    # Readiness is called by probes on a timer (the admin page polls every
    # 60s, Kubernetes typically every 10s). Logging on every call would bury
    # the signal in repeats, so log only when the failing set actually
    # changes — including the transition back to healthy.
    if degraded != _last_degraded_llm:
        if degraded:
            logger.warning("llm_provider_degraded", providers=degraded)
        else:
            logger.info("llm_provider_recovered", was=_last_degraded_llm)
        _last_degraded_llm = degraded
    checks["llm"] = {
        "status": "unavailable" if degraded else "ok",
        "tier": OPTIONAL,
    }

    critical_failed = sorted(n for n, c in checks.items() if c["tier"] == CRITICAL and c["status"] != "ok")
    optional_failed = sorted(n for n, c in checks.items() if c["tier"] == OPTIONAL and c["status"] != "ok")

    if critical_failed:
        overall, http_status = "not_ready", 503
        logger.error("readiness_not_ready", critical_failed=critical_failed)
    elif optional_failed:
        overall, http_status = "degraded", 200
        logger.warning("readiness_degraded", optional_unavailable=optional_failed)
    else:
        overall, http_status = "ok", 200

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "critical_failed": critical_failed,
            "optional_unavailable": optional_failed,
            "checks": checks,
        },
    )
