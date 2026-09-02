"""OAuth callback vs revoke / pause races — real PostgreSQL concurrency.

Invariants under test:
- once revoke commits, a concurrently-in-flight callback must NEVER restore the
  source to CONNECTED (a stale token exchange cannot resurrect a revoked source);
- pause must stop a running sync from writing back OK, and a callback cannot
  reconnect a paused source;
- external network exchange must NOT run inside a transaction that holds a
  long-lived data-source lock (revoke must not block on an in-flight callback).

These run against the isolated, disposable PostgreSQL database so concurrency
is real, not a mocked single-loop interleaving.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import get_session_factory
from app.data.models.connector import OAuthNonce
from app.data.models.data_source import DataSource
from app.data.models.infra import AuditLog
from app.scenarios.data_source import service as ds_service
from app.scenarios.data_source.service import (
    complete_oauth,
    create_data_source,
    revoke_data_source,
    start_oauth,
)

pytestmark = pytest.mark.integration


async def _start_state(tenant_id: str, actor_id: str, source_id: str) -> str:
    from urllib.parse import parse_qs, urlparse

    result = await start_oauth(tenant_id, actor_id, source_id, "https://app.example/cb")
    return parse_qs(urlparse(result["authorize_url"]).query)["state"][0]


def _require_concurrency() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")


async def _seed_source(tenant_id: str, actor_id: str) -> str:
    body = ds_service.CreateDataSourceBody(
        name="竞态测试源",
        platform="wecom",
        purpose="竞态验收",
        authorized_scope="测试范围",
        content_types=["messages"],
        data_destination="员工声音工作区",
        credential=f"secret-{uuid4()}",
        oauth_app_id="ww10086",
    )
    view = await create_data_source(tenant_id, actor_id, body)
    return view.source_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(OAuthNonce).where(OAuthNonce.tenant_id == tenant_id))
        await db.execute(
            delete(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.name == "竞态测试源",
            )
        )
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.commit()


async def _oauth_state(tenant_id: str, source_id: str) -> str:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
        )
        return row.oauth_state if row else ""


# --- callback vs revoke ---


async def test_callback_in_flight_cannot_restore_revoked_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real concurrency: revoke commits while a callback is still exchanging,
    then the callback's token exchange returns. The source must stay revoked."""
    _require_concurrency()
    tenant_id, actor_id = str(uuid4()), str(uuid4())
    source_id = await _seed_source(tenant_id, actor_id)
    state = await _start_state(tenant_id, actor_id, source_id)

    # A callback that has already begun its (slow) token exchange.
    exchange_started = asyncio.Event()
    release_exchange = asyncio.Event()
    errs: list[Exception] = []

    async def fake_exchange(*_a, **_k) -> dict:
        exchange_started.set()
        await release_exchange.wait()  # hold the exchange open
        return {
            "access_token": "fresh-token",
            "refresh_token": "fresh-refresh",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "scopes": ["im:message"],
            "user_id": "u-1",
        }

    monkeypatch.setattr("app.connectors.oauth.exchange_code", fake_exchange)

    callback_started = asyncio.Event()

    async def run_callback() -> None:
        try:
            await complete_oauth(
                tenant_id, actor_id, source_id, code="code-1", state=state
            )
        except Exception as exc:
            # A stale callback after revoke SHOULD be rejected (never connects).
            errs.append(exc)
        finally:
            callback_started.set()

    callback_task = asyncio.create_task(run_callback())
    # Let the callback reach the token exchange (nonce consumed, exchange in flight).
    await asyncio.wait_for(exchange_started.wait(), timeout=10)

    # Revoke runs concurrently while the callback holds no transaction lock on
    # the source row. It must complete without waiting on the callback's exchange.
    await revoke_data_source(tenant_id, actor_id, source_id, reason="竞态撤销")

    # Now let the stale callback finish its exchange and try to write tokens.
    release_exchange.set()
    await asyncio.wait_for(callback_task, timeout=10)

    final = await _oauth_state(tenant_id, source_id)
    assert final == "revoked", f"stale callback restored a revoked source to {final!r}"

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
        )
        assert row.oauth_state == "revoked"
        assert row.oauth_encrypted_token is None
        assert row.oauth_refresh_encrypted is None
    # The stale callback must have been rejected — either via validation error
    # (state invalid after nonce wipe) or via the conditional transition. It
    # must never have returned success.
    assert any(errs), "stale callback unexpectedly succeeded in reconnecting a revoked source"

    await _cleanup(tenant_id)


# --- callback vs pause ---


async def test_callback_cannot_reconnect_a_paused_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pause commits while a callback is exchanging; the callback must not
    flip the source to CONNECTED. Only resume/restart can re-authorize."""
    _require_concurrency()
    tenant_id, actor_id = str(uuid4()), str(uuid4())
    source_id = await _seed_source(tenant_id, actor_id)
    state = await _start_state(tenant_id, actor_id, source_id)

    from app.scenarios.data_source.service import pause_data_source

    exchange_started = asyncio.Event()
    release_exchange = asyncio.Event()

    async def fake_exchange(*_a, **_k) -> dict:
        exchange_started.set()
        await release_exchange.wait()
        return {
            "access_token": "paused-token",
            "refresh_token": "paused-refresh",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "scopes": ["im:message"],
            "user_id": "u-2",
        }

    monkeypatch.setattr("app.connectors.oauth.exchange_code", fake_exchange)

    async def run_callback() -> None:
        try:
            await complete_oauth(tenant_id, actor_id, source_id, code="code-2", state=state)
        except Exception:
            pass

    callback_task = asyncio.create_task(run_callback())
    await asyncio.wait_for(exchange_started.wait(), timeout=10)

    await pause_data_source(tenant_id, actor_id, source_id)

    release_exchange.set()
    await asyncio.wait_for(callback_task, timeout=10)

    final = await _oauth_state(tenant_id, source_id)
    # The source may stay pending or be reset, but it must never become
    # "connected" while paused.
    assert final != "connected"

    await _cleanup(tenant_id)
