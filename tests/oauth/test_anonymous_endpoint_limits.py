"""方案 §WP3：OAuth 匿名端点的限流。

维度与阈值的取舍
----------------
``authorize`` / ``token`` / ``introspect`` 都早于任何身份被调用，能拿到的稳定维度
只有来源 IP 与（对 token 而言）请求里的 ``client_id``。本文件断言三件事：

1. 这三个端点确实在限流范围内（而不是"看起来配了但实际没接上"）；
2. 发现端点**不在**范围内 —— 它被限流会让客户端在最脆弱的那一刻（还没有任何
   凭据时）走不下去；
3. 429 带 ``Retry-After``。没有它，客户端唯一合理的反应就是立刻重试，
   于是限流本身变成了放大流量的东西。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.oauth import rate_limit as rate_limit_module
from app.oauth.main import create_oauth_app
from app.shared.errors import RateLimitError


class _AlwaysOverLimiter:
    async def check_bucket(self, bucket: str, key: str, limit: int) -> None:
        raise RateLimitError("请求过于频繁，请稍后再试")


class _RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def check_bucket(self, bucket: str, key: str, limit: int) -> None:
        self.calls.append((bucket, key, limit))


def _install(monkeypatch: pytest.MonkeyPatch, limiter: Any) -> None:
    monkeypatch.setattr(rate_limit_module, "RateLimiter", lambda *a, **k: limiter)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_oauth_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "expected_bucket"),
    [
        ("/oauth/authorize", "oauth-authorize-ip"),
        ("/oauth/token", "oauth-token-ip"),
        ("/oauth/introspect", "oauth-introspect-ip"),
    ],
)
def test_the_anonymous_endpoints_are_throttled_by_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, expected_bucket: str
) -> None:
    recorder = _RecordingLimiter()
    _install(monkeypatch, recorder)
    client.post(path, data={"client_id": "workbuddy"})
    assert [bucket for bucket, _, _ in recorder.calls] == [expected_bucket]


def test_discovery_endpoints_are_never_throttled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """发现端点被限流 = 客户端在还没有任何凭据时就被挡住，发现流程死锁。"""
    recorder = _RecordingLimiter()
    _install(monkeypatch, recorder)
    response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    assert recorder.calls == []


def test_jwks_is_never_throttled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLimiter()
    _install(monkeypatch, recorder)
    assert client.get("/.well-known/jwks.json").status_code == 200
    assert recorder.calls == []


def test_over_limit_returns_429_with_retry_after(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _AlwaysOverLimiter())
    response = client.post("/oauth/token", data={"client_id": "workbuddy"})
    assert response.status_code == 429
    assert response.headers.get("retry-after")


def test_under_limit_requests_are_served(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """限流接上之后，正常流量不能被误伤 —— 这是最容易出的一类回归。"""
    _install(monkeypatch, _RecordingLimiter())
    response = client.post("/oauth/introspect", data={"client_id": "workbuddy", "token": "x"})
    assert response.status_code != 429


def test_the_limit_is_zero_disables_the_bucket(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """阈值配 0 = 显式关闭（运维的逃生口），不能变成"0 每分钟"即全部拒绝。"""
    recorder = _RecordingLimiter()
    _install(monkeypatch, recorder)
    monkeypatch.setattr(rate_limit_module.settings, "oauth_introspect_per_minute_per_ip", 0)
    response = client.post("/oauth/introspect", data={"client_id": "workbuddy", "token": "x"})
    assert response.status_code != 429
    assert recorder.calls == []
