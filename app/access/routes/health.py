"""HRBP AI Workbench — liveness and blast-radius-aware readiness probes."""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 5.0
CRITICAL = "critical"
OPTIONAL = "optional"


@router.get("/health")
async def health_check():
    """Liveness probe — always returns ok if the process is alive."""
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


async def _check_database() -> None:
    from sqlalchemy import text

    from app.data.database import get_engine

    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.close()


async def _check_milvus() -> None:
    from app.rag.storage.milvus import MilvusStore

    await MilvusStore().check_connection_async()


async def _check_minio() -> None:
    from app.rag.storage.object_store import ObjectStore

    await ObjectStore().check_connection_async()


async def _run_check(name: str, tier: str, probe) -> dict[str, str]:
    try:
        await asyncio.wait_for(probe(), timeout=CHECK_TIMEOUT_SECONDS)
        return {"status": "ok", "tier": tier}
    except TimeoutError:
        logger.warning("readiness_check_timeout", dependency=name, tier=tier, timeout_seconds=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        log = logger.error if tier == CRITICAL else logger.warning
        log(f"{name}_unavailable", tier=tier, error=str(exc))
    return {"status": "error" if tier == CRITICAL else "unavailable", "tier": tier}


@router.get("/ready")
async def readiness_check():
    """Return an orchestrator-friendly readiness verdict.

    Critical dependencies produce HTTP 503; optional dependencies produce a
    useful degraded HTTP 200 response. The public body contains no topology.
    """
    checks = {
        "database": await _run_check("database", CRITICAL, _check_database),
        "redis": await _run_check("redis", CRITICAL, _check_redis),
        "milvus": await _run_check("milvus", OPTIONAL, _check_milvus),
        "minio": await _run_check("minio", OPTIONAL, _check_minio),
    }
    embedding_configured = bool(settings.embedding_base_url and settings.effective_embedding_api_key)
    if not embedding_configured:
        logger.warning("embedding_unconfigured", detail="missing endpoint or API key")
    checks["embedding"] = {"status": "ok" if embedding_configured else "unavailable", "tier": OPTIONAL}

    critical_failed = sorted(name for name, check in checks.items() if check["tier"] == CRITICAL and check["status"] != "ok")
    optional_unavailable = sorted(name for name, check in checks.items() if check["tier"] == OPTIONAL and check["status"] != "ok")
    if critical_failed:
        status, http_status = "not_ready", 503
    elif optional_unavailable:
        status, http_status = "degraded", 200
    else:
        status, http_status = "ok", 200

    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "critical_failed": critical_failed,
            "optional_unavailable": optional_unavailable,
            "checks": checks,
        },
    )
