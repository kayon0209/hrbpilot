"""OAuth CSRF state: one-time, expiring, tenant/source/actor-bound nonce.

Security contract under test:
- a callback with the correct state succeeds exactly once,
- missing/wrong/expired/consumed/cross-bound states are rejected,
- a rejected state NEVER reaches the provider token exchange,
- a successful callback consumes the nonce (replay is impossible),
- revoke wipes every token ciphertext column and invalidates nonces.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.connector import OAuthNonce
from app.data.models.data_source import DataSource
from app.data.models.infra import AuditLog
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(tenant_id: str, admin_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": admin_id,
                "role": "admin",
                "tenant_id": tenant_id,
                "email": f"{admin_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


def _payload(**overrides) -> dict:
    payload = {
        "name": "OAuth CSRF 测试源",
        "platform": "wecom",
        "purpose": "CSRF 验收",
        "authorized_scope": "测试范围",
        "content_types": ["messages"],
        "data_destination": "员工声音工作区",
    }
    payload.update(overrides)
    return payload


def _state_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parse_qs(parsed.query)["state"][0]


def _create_source(client: TestClient, headers: dict, platform: str = "wecom") -> str:
    created = client.post(
        "/api/data-sources",
        headers=headers,
        json=_payload(
            platform=platform,
            credential=f"secret-{uuid4()}",
            oauth_app_id="ww10086" if platform == "wecom" else "cli_abc",
        ),
    )
    assert created.status_code == 200, created.text
    return created.json()["source_id"]


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(OAuthNonce).where(OAuthNonce.tenant_id == tenant_id))
        await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.commit()


def _start_oauth(client: TestClient, headers: dict, source_id: str) -> str:
    started = client.post(
        f"/api/data-sources/{source_id}/oauth-start",
        headers=headers,
        json={"redirect_uri": "https://app.example/callback"},
    )
    assert started.status_code == 200, started.text
    return _state_from_url(started.json()["authorize_url"])


def _fake_tokens(_spec=None, _app_id=None, _app_secret=None, _code=None) -> dict:
    """Deterministic token material; never logged, never echoed by the API."""
    from datetime import UTC as _UTC

    return {
        "access_token": "fake-access-token-material",
        "refresh_token": "fake-refresh-token-material",
        "expires_at": datetime.now(_UTC) + timedelta(hours=1),
        "scopes": ["im:message"],
        "user_id": "u-csrf-test",
    }


async def _fake_exchange(*_args, **_kwargs) -> dict:
    return _fake_tokens()


def _stub_exchange(monkeypatch: pytest.MonkeyPatch, exchanges: list[str]):
    async def fake_exchange(*args, **kwargs) -> dict:
        exchanges.append("exchange")
        return _fake_tokens()

    monkeypatch.setattr("app.connectors.oauth.exchange_code", fake_exchange)


def test_oauth_callback_with_correct_state_succeeds_and_consumes_nonce(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    exchanges: list[str] = []
    _stub_exchange(monkeypatch, exchanges)
    try:
        source_id = _create_source(client, headers)
        state = _start_oauth(client, headers, source_id)

        callback = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert callback.status_code == 200, callback.text
        assert callback.json()["oauth_state"] == "connected"
        assert exchanges == ["exchange"], "token exchange must run once for a valid state"

        # The nonce is consumed: a second use must be rejected WITHOUT exchange.
        replay = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert replay.status_code == 422, replay.text
        assert "已被使用" in replay.json()["message"]
        assert exchanges == ["exchange"], "replayed nonce must not reach token exchange"
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_callback_missing_state_is_rejected_without_exchange(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    exchanges: list[str] = []
    _stub_exchange(monkeypatch, exchanges)
    try:
        source_id = _create_source(client, headers)
        _start_oauth(client, headers, source_id)

        callback = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1"},  # state missing
        )
        assert callback.status_code == 422, callback.text
        assert exchanges == [], "missing state must never reach token exchange"
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_callback_wrong_state_is_rejected_without_exchange(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    exchanges: list[str] = []
    _stub_exchange(monkeypatch, exchanges)
    try:
        source_id = _create_source(client, headers)
        _start_oauth(client, headers, source_id)

        callback = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": "totally-wrong-state"},
        )
        assert callback.status_code == 422, callback.text
        assert exchanges == [], "wrong state must never reach token exchange"
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_callback_expired_state_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    exchanges: list[str] = []
    _stub_exchange(monkeypatch, exchanges)
    try:
        source_id = _create_source(client, headers)
        state = _start_oauth(client, headers, source_id)

        async def expire_nonce() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                await db.execute(
                    __import__("sqlalchemy")
                    .update(OAuthNonce)
                    .where(OAuthNonce.tenant_id == tenant_id, OAuthNonce.source_id == source_id)
                    .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
                )
                await db.commit()

        asyncio.run(expire_nonce())

        callback = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert callback.status_code == 422, callback.text
        assert "已过期" in callback.json()["message"]
        assert exchanges == [], "expired state must never reach token exchange"

        # An expired nonce cannot be resurrected even if replayed.
        replay = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert replay.status_code == 422
        assert exchanges == []
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_callback_state_is_scoped_to_source_and_tenant(client: TestClient) -> None:
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    admin_a, admin_b = str(uuid4()), str(uuid4())
    headers_a = {"Authorization": f"Bearer {_token(tenant_a, admin_a)}"}
    headers_b = {"Authorization": f"Bearer {_token(tenant_b, admin_b)}"}
    try:
        source_a = _create_source(client, headers_a)
        source_b = _create_source(client, headers_b, platform="feishu")
        state_a = _start_oauth(client, headers_a, source_a)
        _start_oauth(client, headers_b, source_b)

        # State minted for source A cannot drive source B in the same tenant.
        cross_source = client.post(
            f"/api/data-sources/{source_b}/oauth-callback",
            headers=headers_b,
            json={"code": "auth-code-1", "state": state_a},
        )
        assert cross_source.status_code == 422, cross_source.text

        # State minted in tenant A cannot drive the same-shaped source in tenant B.
        cross_tenant = client.post(
            f"/api/data-sources/{source_b}/oauth-callback",
            headers=headers_b,
            json={"code": "auth-code-1", "state": state_a},
        )
        assert cross_tenant.status_code == 422
    finally:
        asyncio.run(_cleanup(tenant_a))
        asyncio.run(_cleanup(tenant_b))


def test_revoke_wipes_token_ciphertext_and_invalidates_nonce(client: TestClient) -> None:
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        source_id = _create_source(client, headers)
        state = _start_oauth(client, headers, source_id)

        revoked = client.post(
            f"/api/data-sources/{source_id}/revoke",
            headers=headers,
            json={"reason": "安全审查撤销"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["oauth_state"] == "revoked"

        # A stale callback against a revoked source must fail before exchange.
        stale = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert stale.status_code == 422, stale.text
        assert "已撤销" in stale.json()["message"]

        async def load_row():
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                row = await db.scalar(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
                nonce_count = await db.scalar(
                    select(__import__("sqlalchemy").func.count())
                    .select_from(OAuthNonce)
                    .where(OAuthNonce.tenant_id == tenant_id, OAuthNonce.source_id == source_id)
                )
            return row, nonce_count

        row, nonce_count = asyncio.run(load_row())
        assert row.credential_encrypted is None
        assert row.oauth_encrypted_token is None
        assert row.oauth_refresh_encrypted is None
        assert row.oauth_expires_at is None
        assert row.oauth_connected_at is None
        assert row.oauth_scopes is None
        assert row.oauth_user_id is None
        assert row.oauth_state == "revoked"
        assert nonce_count == 0
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_revoke_connected_source_clears_every_token_column(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB-level assertion: token ciphertext columns must be NULL after revoke."""
    tenant_id, admin_id = str(uuid4()), str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    _stub_exchange(monkeypatch, [])
    try:
        source_id = _create_source(client, headers)
        state = _start_oauth(client, headers, source_id)
        callback = client.post(
            f"/api/data-sources/{source_id}/oauth-callback",
            headers=headers,
            json={"code": "auth-code-1", "state": state},
        )
        assert callback.status_code == 200, callback.text
        assert callback.json()["oauth_state"] == "connected"

        async def assert_connected_has_tokens() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                row = await db.scalar(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
                assert row.oauth_encrypted_token is not None
                assert row.oauth_refresh_encrypted is not None

        asyncio.run(assert_connected_has_tokens())

        revoked = client.post(
            f"/api/data-sources/{source_id}/revoke",
            headers=headers,
            json={"reason": "撤销连接源"},
        )
        assert revoked.status_code == 200, revoked.text

        async def assert_clean() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                row = await db.scalar(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
                assert row.credential_encrypted is None
                assert row.oauth_encrypted_token is None
                assert row.oauth_refresh_encrypted is None
                assert row.oauth_expires_at is None
                assert row.oauth_connected_at is None
                assert row.oauth_scopes is None
                assert row.oauth_user_id is None
                assert row.oauth_state == "revoked"

        asyncio.run(assert_clean())
    finally:
        asyncio.run(_cleanup(tenant_id))
