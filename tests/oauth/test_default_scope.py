"""T2 授权默认 scope（OAUTH_DEFAULT_SCOPE）—— 申请侧的套餐兜底。

锁定三件事（任务书 2026-09-15 T2 路线 B 的安全边界）：

1. **未配置时维持既有回退**：授权请求未携带 scope → 客户端注册全集
   （不是"仅基线"——源码现状就是全集，任务书对该现状的描述与实测证据在
   ``docs/ops/2026-09-15-execution-record.md`` 里对账）；
2. **配置后成为申请套餐**：默认值 ∩ 客户端注册范围（默认不得放大客户端上限），
   且 DCR/CIMD 注册、未绑定租户的客户端**永远**拿不到默认发放的写权限；
3. **默认 scope 不绕过判定**：这里发出的 scope 只是"申请"；能不能调用工具
   仍由 authorize_tool_call 的三重交集决定（有既有测试守护，此处只锁申请侧）。
"""

from __future__ import annotations

import pytest

from app.access.scopes import Scope
from app.config.settings import Settings
from app.data.models.oauth import UNBOUND_TENANT
from app.oauth.clients import validate_client_metadata
from app.oauth.routes.authorize import _validate_authorization_request

REDIRECT_URI = "http://127.0.0.1:9000/callback"
ALL_SCOPES = " ".join(sorted(scope.value for scope in Scope))
QUERY_TIER = " ".join(
    sorted(
        {
            Scope.PROFILE_READ.value,
            Scope.POLICY_READ.value,
            Scope.CASE_READ.value,
            Scope.APPROVAL_READ.value,
        }
    )
)


def _client(*, tenant_id: str = "default", scopes: str = ALL_SCOPES) -> object:
    return validate_client_metadata(
        {
            "redirect_uris": [REDIRECT_URI],
            "scope": scopes,
        },
        client_id="default-scope-test",
        registration_source="dcr",
        tenant_id=tenant_id,
    )


def _params(**override) -> dict:
    from app.config.settings import settings

    params = {
        "response_type": "code",
        "client_id": "default-scope-test",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
        "resource": settings.mcp_resource_url,
    }
    params.update(override)
    return params


def _validate(client, params) -> frozenset[str]:
    """返回该请求最终被授权的 scope 集合（从 AuthorizationRequest.scope 解析）。"""
    request = _validate_authorization_request(params, client, REDIRECT_URI)
    return frozenset(part for part in request.scope.split(" ") if part)


def test_unset_default_keeps_legacy_fallback_to_client_scopes(monkeypatch) -> None:
    """未配置 OAUTH_DEFAULT_SCOPE：未带 scope 的请求仍拿到客户端注册全集。"""
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=""))
    client = _client()
    granted = _validate(client, _params())  # 不带 scope
    assert granted == frozenset(scope.value for scope in Scope)


def test_configured_default_becomes_the_requested_tier(monkeypatch) -> None:
    """配置查询版后：未带 scope 的请求申请查询版 4 项。"""
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=QUERY_TIER))
    client = _client()
    granted = _validate(client, _params())
    assert granted == frozenset(QUERY_TIER.split(" "))
    assert Scope.CASE_PROPOSE.value not in granted, "查询版默认不得带写权限"


def test_default_never_exceeds_the_client_registration(monkeypatch) -> None:
    """默认套餐 ∩ 客户端注册范围 —— 配置不得放大客户端上限。"""
    narrow = " ".join(sorted({Scope.PROFILE_READ.value, Scope.POLICY_READ.value}))
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=QUERY_TIER))
    client = _client(scopes=narrow)
    granted = _validate(client, _params())
    assert granted == frozenset(narrow.split(" "))


def test_unbound_dcr_client_never_gets_write_scope_by_default(monkeypatch) -> None:
    """DCR 注册、未绑定租户的客户端：即使默认套餐含写权限，也要剔除它。"""
    full = " ".join(sorted({*QUERY_TIER.split(" "), Scope.CASE_PROPOSE.value}))
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=full))
    client = _client(tenant_id=UNBOUND_TENANT)
    granted = _validate(client, _params())
    assert Scope.CASE_PROPOSE.value not in granted, "未绑定租户的客户端不得默认获得写权限"
    assert granted == frozenset(QUERY_TIER.split(" "))


def test_bound_tenant_client_may_receive_configured_write_scope(monkeypatch) -> None:
    """已绑定租户的客户端不受剔除影响 —— 剔除只针对"没有租户背书"的那类。"""
    full = " ".join(sorted({*QUERY_TIER.split(" "), Scope.CASE_PROPOSE.value}))
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=full))
    client = _client(tenant_id="default")
    granted = _validate(client, _params())
    assert Scope.CASE_PROPOSE.value in granted


def test_explicit_scope_request_still_wins(monkeypatch) -> None:
    """客户端显式带 scope 时，默认配置完全不参与 —— 显式申请优先。"""
    monkeypatch.setattr("app.oauth.routes.authorize.settings", Settings(oauth_default_scope_raw=QUERY_TIER))
    client = _client()
    granted = _validate(client, _params(scope=f"{Scope.PROFILE_READ.value} {Scope.POLICY_READ.value}"))
    assert granted == {Scope.PROFILE_READ.value, Scope.POLICY_READ.value}


def test_misconfigured_default_scope_fails_at_startup() -> None:
    """拼错的 scope 在 Settings 校验期就报错，不静默退化。"""
    with pytest.raises(ValueError, match="unknown scope"):
        Settings(oauth_default_scope_raw="hrb:policy:read hrb:typo:scope")
