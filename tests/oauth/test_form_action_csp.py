"""授权页的 ``form-action`` 必须显式放行**该客户端自己的回调源**。

为什么需要这一条（真实故障，已被多方复现）
--------------------------------------------
同意页的表单 POST 之后，服务端 302 回客户端回调。**Chrome 与 Safari 会把
``form-action`` 也施加到这次重定向**（Firefox 不会）—— 于是只有 ``'self'`` 时，
"跳回 loopback 回调"被浏览器静默拦掉：页面停在原地、什么都不发生，而服务端日志里
授权码已经签发（``oauth_authorization_granted``）。loopback 客户端（RFC 8252）回调
用的是**随机端口**，对授权页必然跨源，所以必须逐个显式列出。

安全边界
--------
只放行**一个**源，且它必须来自已经过 ``redirect_uri_matches`` 校验的地址；不做通配、
不信任请求里的原始字符串。凡是形态不合法（含空格/分号/引号等能拼接指令的字符）的
一律**丢弃** —— 宁可跳转被拦，也不放出一个被拼接过的 CSP。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.oauth.clients import validate_client_metadata
from app.oauth.html import render_page
from app.oauth.routes.authorize import _callback_form_action_origin
from app.oauth.sessions import AsSession

_LOOPBACK_CALLBACK = "http://127.0.0.1:61565/oauth/callback"


def _csp(response: Any) -> str:
    return response.headers["content-security-policy"]


# --------------------------------------------------------------------- 纯函数层


def test_the_default_policy_is_self_only() -> None:
    """不传回调源时保持最严：只有 ``'self'``（错误页等无跳转场景）。"""
    html = render_page("error.html", message="x", detail="")
    assert "form-action 'self';" in _csp(html)


def test_exactly_one_callback_origin_is_appended() -> None:
    html = render_page("login.html", client_name="c", params={}, form_action_origins=["http://127.0.0.1:61565"])
    csp = _csp(html)
    assert "form-action 'self' http://127.0.0.1:61565;" in csp
    # 不做通配：端口必须逐字列出，不能出现 localhost:* 这类写法
    assert "127.0.0.1:*" not in csp


def test_injection_attempts_are_dropped_instead_of_serialized() -> None:
    """能拼接指令的字符一律丢弃 —— 只留下 ``'self'``。"""
    for evil in (
        "http://127.0.0.1:1; script-src *",
        "http://127.0.0.1:1 'unsafe-inline'",
        "http://127.0.0.1:1,x",
        '"http://x"',
        "http://127.0.0.1:1\nx",
    ):
        csp = _csp(render_page("login.html", client_name="c", params={}, form_action_origins=[evil]))
        assert csp.endswith("form-action 'self'; base-uri 'none'"), evil


def test_custom_scheme_callbacks_are_written_as_a_scheme_source() -> None:
    """WorkBuddy 用私有协议：CSP 里写 ``workbuddy:``（不能带 //host）。"""
    html = render_page("login.html", client_name="c", params={}, form_action_origins=["workbuddy:"])
    assert "form-action 'self' workbuddy:;" in _csp(html)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("http://127.0.0.1:61565/oauth/callback", "http://127.0.0.1:61565"),
        ("https://app.example.com/cb?x=1", "https://app.example.com"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("workbuddy://workbuddy/mcp/callback", "workbuddy:"),
        ("not a uri", None),
        ("", None),
    ],
)
def test_origin_derivation(uri: str, expected: str | None) -> None:
    assert _callback_form_action_origin(uri) == expected


# --------------------------------------------------------------------- 路由层


def _loopback_client() -> Any:
    return validate_client_metadata(
        {"redirect_uris": [_LOOPBACK_CALLBACK], "scope": "hrb:profile:read"},
        client_id="dcr_loopback",
        registration_source="dcr",
    )


_FORM = {
    "response_type": "code",
    "client_id": "dcr_loopback",
    "redirect_uri": _LOOPBACK_CALLBACK,
    "scope": "hrb:profile:read",
    "state": "s",
    "code_challenge": "a" * 43,
    "code_challenge_method": "S256",
    "resource": settings.mcp_resource_url,
}


@pytest.fixture(autouse=True)
def _disable_authorize_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSP 路由断言不应与共享的匿名端点限流桶耦合。"""
    monkeypatch.setattr(settings, "oauth_authorize_per_minute_per_ip", 0)


@pytest.fixture()
def client() -> TestClient:
    from app.oauth.main import create_oauth_app

    return TestClient(create_oauth_app(), raise_server_exceptions=False)


def test_the_login_page_allows_this_clients_callback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """这是本次真实故障的回归测试：CSP 里必须出现该客户端的回调源。"""
    from app.oauth.routes import authorize as authorize_module

    async def _fake_resolve(client_id: str) -> Any:
        return _loopback_client()

    monkeypatch.setattr(authorize_module, "resolve_client", _fake_resolve)

    response = client.get("/oauth/authorize", params=_FORM)
    assert response.status_code == 200
    assert "form-action 'self' http://127.0.0.1:61565;" in _csp(response)


def test_the_consent_page_allows_this_clients_callback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.oauth.routes import authorize as authorize_module

    async def _fake_resolve(client_id: str) -> Any:
        return _loopback_client()

    def _fake_read_session(value: str | None) -> AsSession:
        return AsSession(user_id="u-1", tenant_id="default", role="hrbp", email="a@x", name="A")

    monkeypatch.setattr(authorize_module, "resolve_client", _fake_resolve)
    monkeypatch.setattr(authorize_module, "read_session_cookie", _fake_read_session)

    response = client.get("/oauth/authorize", params=_FORM)
    assert response.status_code == 200
    assert "授权" in response.text
    assert "form-action 'self' http://127.0.0.1:61565;" in _csp(response)


def test_an_unknown_client_keeps_the_strict_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """未校验（未知 client_id）时不得派生任何源 —— 只留 ``'self'``。"""
    from app.oauth.routes import authorize as authorize_module

    async def _unknown_client(_client_id: str) -> None:
        return None

    monkeypatch.setattr(authorize_module, "resolve_client", _unknown_client)
    response = client.get("/oauth/authorize", params={**_FORM, "client_id": "never-registered"})
    assert response.status_code == 400
    assert _csp(response).endswith("form-action 'self'; base-uri 'none'")
