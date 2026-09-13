"""RFC 8414 授权服务器元数据文档。

客户端拿到 RS 的 401 挑战后，第一步是读 Protected Resource Metadata 找到
``authorization_servers``（RFC 9728），第二步就是读本模块产出的这份文档，从里面
拿到 ``authorization_endpoint`` / ``token_endpoint`` / ``jwks_uri``。

为什么端点路径集中在一处
------------------------
这几个路径会同时出现在三个地方：本元数据文档、HTML 表单的 ``action``、以及测试的
断言里。散着写必然漂移，而漂移的表现是"客户端拿到一个 404 的端点地址"—— 又是一次
只在客户端可见的静默失败。
"""

from __future__ import annotations

from typing import Any

from app.access.scopes import Scope
from app.config.settings import settings

AUTHORIZATION_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REGISTRATION_PATH = "/oauth/register"
REVOCATION_PATH = "/oauth/revoke"
INTROSPECTION_PATH = "/oauth/introspect"
JWKS_PATH = "/.well-known/jwks.json"
#: RFC 8414 §3.1：issuer 无路径分量时，well-known 直接挂在根上。
METADATA_PATH = "/.well-known/oauth-authorization-server"


def endpoint_url(path: str) -> str:
    """把 AS 内的路径拼成绝对 URL。"""
    return f"{settings.oauth_issuer}{path}"


def authorization_server_metadata() -> dict[str, Any]:
    """RFC 8414 §2 的授权服务器元数据。

    只声明**真实存在**的端点与能力：``registration_endpoint`` 与
    ``token_endpoint_auth_methods_supported`` 之外的一切都对应一个已注册的路由。
    声明一个会 404 的端点等于对客户端撒谎，而客户端只会把它当作"服务器坏了"。
    ``tests/oauth/test_discovery.py`` 里有一条守卫逐字段验证这件事。
    """
    document: dict[str, Any] = {
        # RFC 8414 §2 唯一的 REQUIRED 字段，且必须与文档自身的 URL 推导结果一致
        # （RFC 9207 的 mix-up 防御依赖这一点）。
        "issuer": settings.oauth_issuer,
        "authorization_endpoint": endpoint_url(AUTHORIZATION_PATH),
        "token_endpoint": endpoint_url(TOKEN_PATH),
        "jwks_uri": endpoint_url(JWKS_PATH),
        "revocation_endpoint": endpoint_url(REVOCATION_PATH),
        "introspection_endpoint": endpoint_url(INTROSPECTION_PATH),
        "scopes_supported": sorted(scope.value for scope in Scope),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # 只支持 public client（无 client secret）+ PKCE。理由见 ``clients.py``：
        # MCP 客户端跑在用户自己的机器上，发给它的 secret 不是 secret。
        # 与其提供一个"看起来更安全、实际是把 secret 公开"的选项，不如只承认这一种。
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        # 内省端点同样只认 self-asserted 的 client_id。它不是"没做认证"的疏漏，
        # 而是 public client 下没有可认证的东西 —— 真正保护内省的是被查询的那个令牌
        # 本身（见 app/oauth/routes/introspect.py）。
        "introspection_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }
    if settings.oauth_enable_dynamic_registration:
        # 仅在开关打开时出现。ADR-0002 §3 把 DCR 定位为**兼容回退**：主路径是 CIMD
        # 与预注册。默认关闭时不声明它，客户端就不会尝试走这条路。
        document["registration_endpoint"] = endpoint_url(REGISTRATION_PATH)
    return document
