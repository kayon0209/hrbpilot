"""``/mcp`` 的鉴权配置必须**自洽**，否则启动即失败。

参见 ``app/config/settings.py`` 的 ``validate_mcp_auth_can_accept_something``。

为什么这条守卫存在
------------------
2026-09-16 实测：``.env`` 里指了 AS（``OAUTH_ISSUER=http://localhost:8002``），
但 ``MCP_AUTHORIZATION_SERVERS`` 留空 —— 于是 RS 的信任列表是空的，**所有**外部令牌
被判 ``malformed``。日志里只有一行 ``mcp_token_rejected reason=malformed``，而
``malformed`` 把"签名不过 / 过期 / audience 不符 / issuer 不在信任列表"揉成一个值，
运维要从它逆推出"其实少配了一个环境变量"。

这两条矛盾在启动期是**确定的**，没有理由留到第一个客户端来撞 401。

同样重要：守卫检查的是**矛盾**，不是"必须填"。单端口部署（AS 挂在 app 自己身上）、
仅平台令牌部署都是合法的 —— 无脑要求非空会把它们一起打死。

为什么用 ``model_construct``
--------------------------
完整构造 ``Settings()`` 会触发 ``validate_environment`` 等一整套校验，要求真实的生产
配置。本文件只关心这一条守卫，所以用 ``model_construct`` 跳过无关校验，再手动调用它。
"""

from __future__ import annotations

import pytest

from app.config.settings import _DEFAULT_OAUTH_ISSUER_ORIGIN, Settings

#: 内置默认：单端口部署时 AS 挂在 app 自己身上，issuer 就是这个值。
#:
#: **从 settings 导入而不是写死字面量**：这个值在 2026-09-16 从 8001 改成 8000
#: （与 app_port / public_base_url 的默认值对齐），写死的那一版让本文件的两个用例
#: 直接失败。守卫的正确性完全取决于"参照物是否与生产一致"，所以参照物只能有一个来源。
_DEFAULT_ISSUER = _DEFAULT_OAUTH_ISSUER_ORIGIN
#: 本机实际拓扑：app 在 8001、AS 独立在 8002。
_LOCAL_AS = "http://localhost:8002"


def _construct(**overrides: object) -> Settings:
    """绕过其余启动期校验，只构造一个最小实例来单独测这条守卫。"""
    base: dict[str, object] = {
        "app_env": "development",
        "oauth_issuer": _DEFAULT_ISSUER,
        "mcp_authorization_servers": "",
        "mcp_accepts_platform_tokens": True,
        "mcp_external_enabled": True,
    }
    base.update(overrides)
    return Settings.model_construct(**base)


# ── 矛盾一：指了 AS，却不信任任何 issuer ─────────────────────────────────────


def test_pointing_at_an_as_without_trusting_it_fails() -> None:
    """本机事故的原始形态：OAUTH_ISSUER 指向真实 AS，信任列表为空。"""
    instance = _construct(oauth_issuer=_LOCAL_AS, mcp_authorization_servers="")

    with pytest.raises(ValueError, match="MCP_AUTHORIZATION_SERVERS"):
        instance.validate_mcp_auth_can_accept_something()


def test_error_message_names_both_variables_and_the_fix() -> None:
    """报错必须直接说清该怎么改，否则运维还是要自己拼线索。"""
    instance = _construct(oauth_issuer=_LOCAL_AS)

    with pytest.raises(ValueError) as excinfo:
        instance.validate_mcp_auth_can_accept_something()

    message = str(excinfo.value)
    assert "OAUTH_ISSUER" in message
    assert "MCP_AUTHORIZATION_SERVERS" in message
    assert _LOCAL_AS in message, "应给出可直接复制的取值"


def test_pointing_at_an_as_and_trusting_it_is_fine() -> None:
    instance = _construct(oauth_issuer=_LOCAL_AS, mcp_authorization_servers=_LOCAL_AS)

    assert instance.validate_mcp_auth_can_accept_something() is instance


# ── 矛盾二：两种凭据都不可用 ─────────────────────────────────────────────────


def test_no_trusted_issuer_and_no_platform_tokens_fails() -> None:
    """外部令牌无人可信、平台令牌又被关 → /mcp 无任何可用凭据。"""
    instance = _construct(
        oauth_issuer=_DEFAULT_ISSUER,
        mcp_authorization_servers="",
        mcp_accepts_platform_tokens=False,
    )

    with pytest.raises(ValueError, match="MCP_ACCEPTS_PLATFORM_TOKENS"):
        instance.validate_mcp_auth_can_accept_something()


def test_no_trusted_issuer_with_platform_tokens_is_allowed() -> None:
    """仅有平台令牌是**合法**部署（AS 上线前的过渡态）—— 不得误杀。"""
    instance = _construct(
        oauth_issuer=_DEFAULT_ISSUER,
        mcp_authorization_servers="",
        mcp_accepts_platform_tokens=True,
    )

    assert instance.validate_mcp_auth_can_accept_something() is instance


def test_default_issuer_with_a_trusted_as_is_allowed() -> None:
    """issuer 保持默认、但显式信任了一个 AS：也算自洽。"""
    instance = _construct(oauth_issuer=_DEFAULT_ISSUER, mcp_authorization_servers=_LOCAL_AS)

    assert instance.validate_mcp_auth_can_accept_something() is instance


# ── 出口：明确关掉外部 MCP 时不受这条守卫约束 ────────────────────────────────


def test_turning_external_mcp_off_bypasses_the_guard() -> None:
    """"就是要关掉外部 MCP"是正当决定，不该被守卫拦住。"""
    instance = _construct(
        oauth_issuer=_LOCAL_AS,
        mcp_authorization_servers="",
        mcp_accepts_platform_tokens=False,
        mcp_external_enabled=False,
    )

    assert instance.validate_mcp_auth_can_accept_something() is instance
