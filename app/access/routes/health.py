"""HRBP AI Workbench — health check and readiness endpoints.

/health → liveness (is the app running?)
/ready   → readiness (are all dependencies reachable?)
"""

from fastapi import APIRouter

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Liveness probe — always returns ok if the process is alive."""
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@router.get("/ready")
async def readiness_check():
    """Readiness probe — checks if all critical dependencies are reachable.

    This endpoint is PUBLIC (no auth): a degraded response must not disclose
    internal topology (host names, ports, driver errors). Raw exception text
    only goes to the server log; the public payload carries status only
    (audit 2026-08-31 P2-1).
    """
    checks = {}

    # Database check
    try:
        from sqlalchemy import text

        from app.data.database import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        logger.error("database_unavailable", error=str(e))
        checks["database"] = {"status": "error"}

    # Redis check
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        checks["redis"] = {"status": "ok"}
    except Exception as e:
        logger.warning("redis_unavailable", error=str(e))
        checks["redis"] = {"status": "error"}

    try:
        from app.rag.storage.milvus import MilvusStore

        await MilvusStore().check_connection_async()
        checks["milvus"] = {"status": "ok"}
    except Exception as e:
        logger.warning("milvus_unavailable", error=str(e))
        checks["milvus"] = {"status": "error"}

    try:
        from app.rag.storage.object_store import ObjectStore

        await ObjectStore().check_connection_async()
        checks["minio"] = {"status": "ok"}
    except Exception as e:
        logger.warning("minio_unavailable", error=str(e))
        checks["minio"] = {"status": "error"}

    embedding_configured = bool(settings.embedding_base_url and settings.effective_embedding_api_key)
    if not embedding_configured:
        logger.warning("embedding_unconfigured", detail="missing endpoint or API key")
    checks["embedding"] = {"status": "ok" if embedding_configured else "error"}

    # Overall status
    all_ok = all(c.get("status") == "ok" for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
    }
