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
    """Readiness probe — checks if all critical dependencies are reachable."""
    checks = {}

    # Database check
    try:
        from app.data.database import get_engine
        from sqlalchemy import text
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        logger.error("database_unavailable", error=str(e))
        checks["database"] = {"status": "error", "detail": str(e)}

    # Redis check
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        checks["redis"] = {"status": "ok"}
    except Exception as e:
        logger.warning("redis_unavailable", error=str(e))
        checks["redis"] = {"status": "error", "detail": str(e)}

    # Overall status
    all_ok = all(c.get("status") == "ok" for c in checks.values())
    status_code = 200 if all_ok else 503

    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
    }
