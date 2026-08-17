"""HRBP AI Workbench — database engine and session management.

Async SQLAlchemy with connection pooling.
Tenant context is set per-session for RLS policies.
Engine is created lazily to avoid import-time failures when DB is unavailable.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.config.settings import settings

# Lazy engine initialization — avoid crashing on import if DB isn't reachable
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, _transaction: object, connection: object) -> None:
    """Reapply local RLS context after every commit starts a new transaction."""
    tenant_id = session.info.get("tenant_id")
    if tenant_id:
        connection.execute(  # type: ignore[attr-defined]
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )


def _init_engine() -> None:
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


def _get_factory() -> async_sessionmaker[AsyncSession]:
    """Return the (lazily-initialized) session factory."""
    _init_engine()
    assert _async_session_factory is not None
    return _async_session_factory


def get_engine() -> AsyncEngine:
    """Get the async engine (lazy init)."""
    _init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the async session factory (lazy init)."""
    return _get_factory()


async def make_tenant_session(tenant_id: str) -> AsyncSession:
    """Create a session with the RLS tenant context pre-set.

    Used by non-request code paths (retriever, ingestion worker) that query
    tenant-scoped tables but are not inside a FastAPI request dependency.
    The caller is responsible for closing the session.
    """
    session = _get_factory()()
    session.info["tenant_id"] = tenant_id
    await session.execute(text("SELECT 1"))
    return session


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields a DB session with tenant context for RLS.

    Reads tenant_id from request.state (injected by TenantContextMiddleware
    and/or AuthMiddleware) and sets it as a PostgreSQL session variable so
    that RLS policies filter rows correctly per tenant. FastAPI injects the
    Request automatically.
    """
    session = _get_factory()()
    tenant_id = getattr(request.state, "tenant_id", "default")
    session.info["tenant_id"] = tenant_id
    try:
        await session.execute(text("SELECT 1"))
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session(tenant_id: str = "default") -> AsyncIterator[AsyncSession]:
    """Async generator yielding a tenant-scoped session for non-request callers.

    Used by code paths that run outside a FastAPI request (e.g. auth login
    before a tenant context exists). Mirrors ``get_db`` but takes an explicit
    tenant_id instead of a Request.
    """
    session = _get_factory()()
    session.info["tenant_id"] = tenant_id
    try:
        await session.execute(text("SELECT 1"))
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
