"""WorkBuddy 私有协议回调：显式放行的自定义 scheme。

背景
----
WorkBuddy 内置的 OAuth 管理器**优先**使用
``workbuddy://workbuddy/mcp/connector%3A<source>/oauth/callback``，只有在私有协议
被拒时才回退到 loopback（官方连接器文档）。而 WorkBuddy 是本项目的 P0 客户端 ——
一律拒绝等于让首选接入方式走不通。

为什么可以接受这个放宽
----------------------
本 AS 对所有客户端**强制 PKCE S256**：即使另一个应用抢注了同一 scheme 并截获授权码，
它没有 ``code_verifier``，换不到令牌。加上 redirect_uri 逐字节精确匹配，"抢注 scheme"
从"直接拿到令牌"退化成"拿到一个用不了的码"。

下面守的是放宽的**边界**，而不是放宽本身。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings
from app.oauth.clients import (
    ClientMetadataError,
    _validate_redirect_uri,
    redirect_uri_matches,
)

_WORKBUDDY_CALLBACK = "workbuddy://workbuddy/mcp/connector%3Ahrbpilot/oauth/callback"


def test_a_custom_scheme_is_rejected_by_default() -> None:
    """默认姿态必须是拒绝 —— 这个放宽要有人签字。"""
    assert settings.oauth_custom_redirect_schemes == frozenset()
    with pytest.raises(ClientMetadataError):
        _validate_redirect_uri(_WORKBUDDY_CALLBACK)


def test_an_allowlisted_scheme_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    assert _validate_redirect_uri(_WORKBUDDY_CALLBACK) == _WORKBUDDY_CALLBACK


def test_the_scheme_list_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """URI scheme 比较大小写无关，清单不该更严格。"""
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "WorkBuddy")
    assert _validate_redirect_uri(_WORKBUDDY_CALLBACK) == _WORKBUDDY_CALLBACK


def test_a_scheme_that_is_not_allowlisted_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    with pytest.raises(ClientMetadataError):
        _validate_redirect_uri("evilapp://callback/hijack")


def test_a_custom_scheme_without_a_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``workbuddy:/callback`` 这类没有 host 的写法无法与普通路径区分。"""
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    with pytest.raises(ClientMetadataError):
        _validate_redirect_uri("workbuddy:/oauth/callback")


def test_a_custom_scheme_with_a_fragment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC 6749 §3.1.2 的禁止项对自定义 scheme 同样成立。"""
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    with pytest.raises(ClientMetadataError):
        _validate_redirect_uri("workbuddy://workbuddy/callback#token=leak")


def test_the_port_relaxation_does_not_apply_to_custom_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC 8252 §7.3 的"忽略端口"只对 loopback 成立。

    自定义 scheme 上放开端口等于把精确匹配换成了模糊匹配 —— 而 loopback 之所以能
    放宽，是因为它永远指向用户自己这台机器，这个前提在自定义 scheme 上不存在。
    """
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    registered = [_WORKBUDDY_CALLBACK]
    assert redirect_uri_matches(registered, _WORKBUDDY_CALLBACK) is True
    assert redirect_uri_matches(registered, "workbuddy://workbuddy/mcp/connector%3Aother/oauth/callback") is False


def test_loopback_still_works_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """放宽自定义 scheme 不能影响既有的 loopback 行为。"""
    monkeypatch.setattr(settings, "oauth_custom_redirect_schemes_raw", "workbuddy")
    assert _validate_redirect_uri("http://127.0.0.1:8080/oauth/callback") == "http://127.0.0.1:8080/oauth/callback"
    assert (
        redirect_uri_matches(["http://127.0.0.1:8080/oauth/callback"], "http://127.0.0.1:9999/oauth/callback") is True
    )
