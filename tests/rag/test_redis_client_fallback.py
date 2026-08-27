"""Redis client fallback behavior (Phase 1.3 follow-up).

When Redis is unreachable ``get_redis`` must return None (callers fall back
to in-memory) and tear the failed connection down cleanly instead of
leaving an orphaned future that later raises
"Future exception was never retrieved".
"""

import app.shared.redis_client as rc


async def test_get_redis_returns_none_when_unreachable(monkeypatch):
    monkeypatch.setattr(rc, "_client", None)
    monkeypatch.setattr(rc, "_client_unavailable", False)
    monkeypatch.setattr(rc.settings, "redis_url", "redis://127.0.0.1:1/0")

    client = await rc.get_redis()

    assert client is None
    assert rc._client_unavailable is True


async def test_get_redis_short_circuits_on_same_loop_after_failure(monkeypatch):
    # Failure recorded on loop L: repeated calls on L return None (no hammering).
    import asyncio

    monkeypatch.setattr(rc, "_client", None)
    monkeypatch.setattr(rc, "_client_unavailable", True)
    monkeypatch.setattr(rc, "_client_loop", asyncio.get_running_loop())

    assert await rc.get_redis() is None
