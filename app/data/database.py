"""HRBP AI Workbench — database engine and session management.

Async SQLAlchemy with connection pooling.
Tenant context is set per-session for RLS policies.
Engine is created lazily to avoid import-time failures when DB is unavailable.

The `get_db` dependency reads `tenant_id` from the request state
(set by TenantContextMiddleware / AuthMiddleware) and applies it as
a PostgreSQL session variable so that RLS policies filter correctly.
"""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from app.config.settings import settings

# Lazy engine initialization — avoid crashing on import if DB isn't reachable
_engine = None
_async_session_factory = None


def _init_engine():
    """Create async engine and session factory on first use."""
    global _engine, _async_session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=settings.app_debug,
        )
        _async_session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )


def get_engine():
    """Get the async engine (lazy init)."""
    _init_engine()
    return _engine


async def get_db(request: Request | None = None) -> AsyncSession:
    """FastAPI dependency — yields a DB session with tenant context for RLS.

    Reads tenant_id from request.state (injected by TenantContextMiddleware
    and/or AuthMiddleware) and sets it as a PostgreSQL session variable
    so that RLS policies filter rows correctly per tenant.

    When called from a FastAPI route handler as a dependency, FastAPI
    injects the Request automatically. When called directly (e.g. in
    auth.py), pass request=None and the session gets the default tenant.
    """
    _init_engine()
    session = _async_session_factory()
    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    try:
        await session.execute(
            text("SET app.tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
