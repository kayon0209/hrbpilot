"""授权流程里的租户边界：登录按**客户端的租户**找人，会话跨租户一律不算数。

把这两条闸放在一起测，是因为它们守护同一个不变式的两侧：
登录侧 —— ``authenticate`` 拿到的租户必须来自客户端记录，而不是任何请求可控的值；
同意侧 —— 会话租户与客户端租户不一致时，绝不能签出授权码（那等于令牌上的租户成了
表单可改的字段）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.oauth.clients import validate_client_metadata
from app.oauth.sessions import AsSession


def _acme_client() -> Any:
    return validate_client_metadata(
        {
            "redirect_uris": ["http://127.0.0.1:9/callback"],
            "scope": "hrb:profile:read",
        },
        client_id="prereg-acme",
        registration_source="pre_registered",
        tenant_id="acme",
    )


_FORM = {
    "response_type": "code",
    "client_id": "prereg-acme",
    "redirect_uri": "http://127.0.0.1:9/callback",
    "scope": "hrb:profile:read",
    "state": "s",
    "code_challenge": "a" * 43,
    "code_challenge_method": "S256",
    "resource": settings.mcp_resource_url,
}


@pytest.fixture()
def client() -> TestClient:
    from app.oauth.main import create_oauth_app

    return TestClient(create_oauth_app(), raise_server_exceptions=False)


def test_login_authenticates_in_the_clients_tenant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.oauth.routes import authorize as authorize_module

    captured: dict[str, Any] = {}

    async def _fake_resolve(client_id: str) -> Any:
        return _acme_client()

    async def _fake_authenticate(email: str, password: str, *, tenant_id: str = "default") -> AsSession:
        captured["tenant_id"] = tenant_id
        return AsSession(user_id="u-1", tenant_id=tenant_id, role="hrbp", email=email, name="A")

    monkeypatch.setattr(authorize_module, "resolve_client", _fake_resolve)
    monkeypatch.setattr(authorize_module, "authenticate", _fake_authenticate)

    response = client.post("/oauth/authorize/login", data={**_FORM, "email": "a@x", "password": "p"})
    assert response.status_code == 200
    # 租户必须来自客户端记录 —— 表单里就算塞了 tenant_id 也轮不到它说话。
    assert captured["tenant_id"] == "acme"


def test_a_session_from_another_tenant_cannot_consent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省租户的登录态给 acme 客户端同意 → 回登录页，绝不签授权码。"""
    from app.oauth.routes import authorize as authorize_module

    async def _fake_resolve(client_id: str) -> Any:
        return _acme_client()

    def _fake_read_session(value: str | None) -> AsSession:
        return AsSession(user_id="u-1", tenant_id="default", role="hrbp", email="a@x", name="A")

    async def _must_not_issue(**kwargs: Any) -> str:
        raise AssertionError("跨租户会话不得签出授权码")

    monkeypatch.setattr(authorize_module, "resolve_client", _fake_resolve)
    monkeypatch.setattr(authorize_module, "read_session_cookie", _fake_read_session)
    monkeypatch.setattr(authorize_module, "issue_authorization_code", _must_not_issue)

    response = client.post("/oauth/authorize/consent", data={**_FORM, "decision": "allow"})
    assert response.status_code == 401
    assert "登录" in response.text


def test_a_matching_tenant_session_can_consent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正向对照：租户一致时签码路径不被这道闸挡住。"""
    from app.oauth.routes import authorize as authorize_module

    async def _fake_resolve(client_id: str) -> Any:
        return _acme_client()

    def _fake_read_session(value: str | None) -> AsSession:
        return AsSession(user_id="u-1", tenant_id="acme", role="hrbp", email="a@x", name="A")

    issued: dict[str, str] = {}

    async def _fake_issue(**kwargs: Any) -> str:
        issued["tenant_id"] = kwargs["tenant_id"]
        return "code-1"

    monkeypatch.setattr(authorize_module, "resolve_client", _fake_resolve)
    monkeypatch.setattr(authorize_module, "read_session_cookie", _fake_read_session)
    monkeypatch.setattr(authorize_module, "issue_authorization_code", _fake_issue)

    # 不跟随重定向：302 的 Location 是客户端回调地址，跟过去只会是 404。
    response = client.post("/oauth/authorize/consent", data={**_FORM, "decision": "allow"}, follow_redirects=False)
    assert response.status_code == 302
    assert issued["tenant_id"] == "acme"
