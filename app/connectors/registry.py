"""Connector platform registry — provider metadata for the first batch.

Each connector declares its OAuth endpoints, scopes and API base so the
generic OAuth client and sync engine stay platform-agnostic. Documentation
references are the official API docs the implementations were written
against; certification levels live in the capability matrix, not here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorSpec:
    platform: str
    label: str
    oauth_authorize_url: str
    oauth_token_url: str
    oauth_scopes: list[str]
    api_base: str
    token_refresh_minutes_before_expiry: int = 5
    supports_webhook: bool = True
    docs_ref: str = ""


WECOM = ConnectorSpec(
    platform="wecom",
    label="企业微信",
    # WeCom self-built app OAuth2 (企业微信开发者中心 - 网页授权登录)
    oauth_authorize_url="https://open.weixin.qq.com/connect/oauth2/authorize",
    oauth_token_url="https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
    oauth_scopes=["snsapi_base", "snsapi_privateinfo"],
    api_base="https://qyapi.weixin.qq.com/cgi-bin",
    docs_ref="https://developer.work.weixin.qq.com/document/",
)

FEISHU = ConnectorSpec(
    platform="feishu",
    label="飞书",
    # Feishu open platform OAuth2 (授权码模式)
    oauth_authorize_url="https://open.feishu.cn/open-apis/authen/v1/index",
    oauth_token_url="https://open.feishu.cn/open-apis/authen/v2/oauth/token",
    oauth_scopes=["docs:document:readonly", "drive:drive:readonly", "im:message"],
    api_base="https://open.feishu.cn/open-apis",
    docs_ref="https://open.feishu.cn/document/",
)

SPECS: dict[str, ConnectorSpec] = {spec.platform: spec for spec in (WECOM, FEISHU)}

# Platforms still at Level 1 (contract-only): they register plans but have no
# OAuth client implementation yet — the honest boundary of this batch.
PLANNED_PLATFORMS = {"dingtalk", "wps365", "exchange", "oa", "hris"}


def spec_for(platform: str) -> ConnectorSpec:
    if platform not in SPECS:
        raise KeyError(f"platform {platform} has no connector implementation")
    return SPECS[platform]
