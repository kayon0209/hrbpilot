"""``OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS`` 在生产/预发必须让进程启动失败。

参见 ``app/oauth/cimd.py`` 的 ``_assert_host_resolves_to_public_ips`` 与
``app/config/settings.py`` 的 ``validate_oauth_cimd_fetch_reach``。

CIMD 的抓取目标完全由未认证的调用方决定（``client_id`` 就是这个 URL），所以这个开关
一旦在生产环境打开，就等于把授权服务器变成一个"替我访问内网"的工具。它**只**用于本地
验收，而本地验收应跑在 ``development`` 下。这里验证的是：在 production / staging 设置它
会**启动即失败**，而不是被悄悄忽略（忽略会让运维在事故之后才发现它从未生效）。

为什么用 ``model_construct``
--------------------------
直接 ``Settings(app_env="production", ...)`` 会触发 ``validate_environment`` 等一系列
校验，而它们要求一整套真实的生产配置（JWT 密钥、MinIO、embedding 等）。本文件只关心
CIMD 这一条守卫，所以用 ``model_construct`` 跳过那些无关校验，再手动调用对应校验器。
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings


def _construct(app_env: str, **overrides: object) -> Settings:
    """绕过其余启动期校验，只构造一个最小实例来单独测这一条守卫。"""
    return Settings.model_construct(app_env=app_env, **overrides)


def test_setting_private_hosts_in_production_fails() -> None:
    instance = _construct("production", oauth_cimd_allowed_private_hosts_raw="127.0.0.1")
    with pytest.raises(ValueError, match="OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS"):
        instance.validate_oauth_cimd_fetch_reach()


def test_setting_private_hosts_in_staging_fails() -> None:
    """staging 在守卫语义里等同于 production（``is_production`` 包含两者）。"""
    instance = _construct("staging", oauth_cimd_allowed_private_hosts_raw="127.0.0.1")
    with pytest.raises(ValueError, match="OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS"):
        instance.validate_oauth_cimd_fetch_reach()


def test_setting_private_hosts_in_development_is_allowed() -> None:
    """development 下允许放宽 —— 本地验收正是在这里跑。"""
    instance = _construct("development", oauth_cimd_allowed_private_hosts_raw="127.0.0.1")
    assert instance.validate_oauth_cimd_fetch_reach() is instance


def test_an_empty_allowlist_is_fine_in_any_env() -> None:
    for env in ("production", "staging", "development"):
        instance = _construct(env, oauth_cimd_allowed_private_hosts_raw="")
        assert instance.validate_oauth_cimd_fetch_reach() is instance
