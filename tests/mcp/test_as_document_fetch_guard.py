"""RS 取 AS 元数据/JWKS 时的地址白名单。

真实故障沉淀（容器部署全线 401）
--------------------------------
``docker-compose`` 给 RS 配了 ``OAUTH_INTERNAL_BASE_URL=http://oauth-as:8000``
（容器内网络里 AS 只能走明文 http），而白名单当时只放行环回 → ``oauth-as`` 被拒 →
JWKS 取不到 → **每个**外部令牌都被判 ``malformed``。症状误导性很强：客户端表现是
"认证失败"，RS 日志里真正的线索是 ``as_document_url_rejected``。

所以这里把"环回"与"运维显式配置的容器内主机"一起锁进白名单，并断言：
任意的第三方明文 http 主机**仍然被拒**（否则任何能改写链路响应的人都能塞公钥）。
"""

from __future__ import annotations

import pytest

from app.access.as_tokens import _is_fetchable_document_url
from app.config.settings import settings


@pytest.fixture(autouse=True)
def _no_internal_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_internal_base_url", "")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8002/.well-known/oauth-authorization-server",
        "http://127.0.0.1:8002/.well-known/jwks.json",
        "http://[::1]:8002/.well-known/jwks.json",
        "https://as.example.com/.well-known/jwks.json",
    ],
)
def test_loopback_and_https_are_fetchable(url: str) -> None:
    assert _is_fetchable_document_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://as.example.com/.well-known/jwks.json",  # 任意明文主机：拒绝
        "http://oauth-as:8000/.well-known/jwks.json",  # 未配置时：容器主机名也拒绝
        "http://user:pw@localhost:8002/jwks.json",  # userinfo
        "ftp://localhost/jwks.json",
        "/.well-known/jwks.json",
        "",
    ],
)
def test_plaintext_non_loopback_and_malformed_are_rejected(url: str) -> None:
    assert _is_fetchable_document_url(url) is False


def test_the_configured_internal_host_is_allowed_over_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """这是修复本体：容器内 AS 地址（明文 http）必须可取。"""
    monkeypatch.setattr(settings, "oauth_internal_base_url", "http://oauth-as:8000")
    assert _is_fetchable_document_url("http://oauth-as:8000/.well-known/jwks.json") is True
    # 但白名单只多这一个主机，别的明文主机不受影响
    assert _is_fetchable_document_url("http://oauth-as.evil.test/jwks.json") is False
    assert _is_fetchable_document_url("http://other-service:8000/jwks.json") is False
