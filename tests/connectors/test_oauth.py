"""OAuth flow for WeCom / Feishu — mocked-provider contract tests."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.connectors import oauth
from app.connectors.credentials import decrypt_credential
from app.connectors.registry import FEISHU, WECOM, spec_for


def test_registry_exposes_first_batch_and_marks_the_rest_planned() -> None:
    assert WECOM.platform == "wecom"
    assert FEISHU.platform == "feishu"
    from app.connectors.registry import PLANNED_PLATFORMS

    assert {"dingtalk", "wps365", "exchange"} <= PLANNED_PLATFORMS
    with pytest.raises(KeyError):
        spec_for("dingtalk")


def test_authorize_url_includes_state_for_csrf_protection() -> None:
    url = oauth.authorize_url(WECOM, "corp-123", "https://app.example/callback", state="st4te")
    assert "state=st4te" in url
    assert "appid=corp-123" in url

    feishu_url = oauth.authorize_url(FEISHU, "cli_abc", "https://app.example/callback", state="st4te")
    assert "state=st4te" in feishu_url
    assert "app_id=cli_abc" in feishu_url


@pytest.mark.asyncio
async def test_wecom_exchange_code_returns_tokens_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "gettoken" in str(request.url):
            return httpx.Response(200, json={"errcode": 0, "access_token": "app-token", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 0, "userid": "zhangsan"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(oauth, "_http_client", lambda: httpx.AsyncClient(transport=transport, timeout=5.0))

    tokens = await oauth.exchange_code(WECOM, "corp-123", "secret", "auth-code")
    assert tokens["access_token"] == "app-token"
    assert tokens["user_id"] == "zhangsan"
    assert tokens["expires_at"] > datetime.now(UTC)
    # The provider secret travels only in the token request, never in logs.
    assert all("secret" not in str(r.url) or "corpsecret" in str(r.url) for r in calls)


@pytest.mark.asyncio
async def test_feishu_exchange_code_parses_token_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "u-access",
                "refresh_token": "u-refresh",
                "expires_in": 6900,
                "scope": "docs:document:readonly",
                "user_id": "ou_123",
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(oauth, "_http_client", lambda: httpx.AsyncClient(transport=transport, timeout=5.0))

    tokens = await oauth.exchange_code(FEISHU, "cli_abc", "app-secret", "code")
    assert tokens["access_token"] == "u-access"
    assert tokens["refresh_token"] == "u-refresh"
    assert tokens["scopes"] == "docs:document:readonly"


@pytest.mark.asyncio
async def test_provider_error_becomes_an_oauth_flow_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.shared.errors import AppError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid credential"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(oauth, "_http_client", lambda: httpx.AsyncClient(transport=transport, timeout=5.0))

    with pytest.raises(AppError) as exc_info:
        await oauth.exchange_code(WECOM, "corp", "bad-secret", "code")
    assert "企业微信" in str(exc_info.value)


def test_encrypt_token_bundle_never_keeps_plaintext() -> None:
    bundle = oauth.encrypt_token_bundle(
        "tenant-001",
        {
            "access_token": "plain-access",
            "refresh_token": "plain-refresh",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "scopes": ["im:message"],
            "user_id": "u1",
        },
    )
    assert b"plain-access" not in bundle["access"]
    assert b"plain-refresh" not in bundle["refresh"]
    assert decrypt_credential("tenant-001", bundle["access"]) == "plain-access"
    assert decrypt_credential("tenant-001", bundle["refresh"]) == "plain-refresh"


def test_needs_refresh_inside_the_refresh_window() -> None:
    soon = datetime.now(UTC) + timedelta(minutes=2)
    later = datetime.now(UTC) + timedelta(hours=2)
    assert oauth.needs_refresh(soon, WECOM) is True
    assert oauth.needs_refresh(later, WECOM) is False
    assert oauth.needs_refresh(None, WECOM) is True
