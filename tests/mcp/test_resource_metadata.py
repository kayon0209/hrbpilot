"""WP2-a：RFC 9728 发现面 —— 元数据文档、401 挑战、以及派生它的配置。

为什么这些断言写得这么细
------------------------
这条链路没有别的验证手段。客户端拿到 401 之后做什么，完全由**我们发出的字节**
决定：挑战头少一个参数、元数据里 ``resource`` 差一个字符，症状都是"客户端静默地
走不通"。而且失败发生在客户端那边 —— 本服务的日志里什么异常都看不到。
所以这里把构造结果钉死到具体字符串。

覆盖的失败模式（都真实存在，不是假想）：
- 挑战头缺 ``resource_metadata`` → 客户端不知道去哪拿令牌，直接放弃；
- 没带凭据却回了 ``error="invalid_token"`` → 客户端以为自己的令牌坏了；
- 未配置 AS 却返回空数组 → 客户端无法区分"没有 AS"与"字段没写"（故字段应缺席）；
- ``public_base_url`` 带路径或尾斜杠 → 拼出 ``//.well-known`` 或改变 well-known 位置；
- 发现端点被中间件拦下 → 三层中间件各有匿名清单，漏改一层即 403。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import ValidationError

from app.access.resource_metadata import protected_resource_metadata, www_authenticate_challenge
from app.access.scopes import Scope
from app.config.settings import Settings, settings
from app.main import create_app

#: 生产校验之外，``Settings`` 在 development 下构造默认值的 hermetic 写法。
_NO_ENV_FILE = {"_env_file": None}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """不进入 ``with`` 块 ⇒ **不触发 lifespan**。

    发现端点与 ``/mcp`` 的无凭据拒绝都发生在中间件层，完全不碰数据库、Milvus 或
    MinIO。为了这几条断言去跑一次基础设施初始化，会让测试变慢且引入与断言无关的
    失败源。
    """
    test_client = TestClient(create_app(), raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()


def _platform_access_token(*, role: str = "hrbp", tenant_id: str = "tenant-a") -> str:
    """平台自签 access token —— 与 ``/mcp`` 当前的过渡期可接受凭据一致。"""
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": "user-1",
                "role": role,
                "tenant_id": tenant_id,
                "email": "user-1@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


# --------------------------------------------------------------------------- #
# 元数据文档
# --------------------------------------------------------------------------- #


def test_metadata_describes_resource_scopes_and_bearer_method() -> None:
    document = protected_resource_metadata()

    assert document["resource"] == settings.mcp_resource_url
    assert document["bearer_methods_supported"] == ["header"]
    # 词表全量、且顺序稳定：客户端会缓存这份文档，随机顺序会产生无意义的 diff。
    assert document["scopes_supported"] == sorted(scope.value for scope in Scope)
    assert document["scopes_supported"] == sorted(document["scopes_supported"])


def test_metadata_omits_authorization_servers_until_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置时字段必须**缺席**，不能是空数组 —— 两者的含义完全不同。"""
    monkeypatch.setattr(settings, "mcp_authorization_servers", "")

    assert "authorization_servers" not in protected_resource_metadata()


def test_metadata_lists_authorization_servers_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "mcp_authorization_servers", "https://idp.example.com/oauth, https://as2.example.com/"
    )

    assert protected_resource_metadata()["authorization_servers"] == [
        "https://idp.example.com/oauth",
        "https://as2.example.com",
    ]


# --------------------------------------------------------------------------- #
# 401 挑战头
# --------------------------------------------------------------------------- #


def test_challenge_without_error_is_a_bare_discovery_hint() -> None:
    """没带凭据不是错误 —— 这正是客户端开始发现流程的入口，不能标成令牌有问题。"""
    assert www_authenticate_challenge() == f'Bearer resource_metadata="{settings.protected_resource_metadata_url}"'


def test_challenge_with_error_keeps_the_discovery_hint() -> None:
    """带 error 时 ``resource_metadata`` 必须**同时**存在：否则客户端知道令牌坏了，
    却仍然不知道该去哪换一个。"""
    assert www_authenticate_challenge(error="invalid_token") == (
        f'Bearer error="invalid_token", resource_metadata="{settings.protected_resource_metadata_url}"'
    )


# --------------------------------------------------------------------------- #
# 配置校验与派生
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/mcp",
        "hrbp.example.com",
        "ftp://hrbp.example.com",
        "https://hrbp.example.com/mcp",  # 带路径：改变 well-known 的解析位置
        "https://hrbp.example.com?a=1",
        "https://hrbp.example.com#x",
        "https://user:pass@hrbp.example.com",
        "http://hrbp.example.com",  # 非环回却用明文
        "https://hrbp .example.com",  # 内部空白：URL 里不可能有空格
    ],
)
def test_public_base_url_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL"):
        Settings(**_NO_ENV_FILE, public_base_url=value)  # type: ignore[arg-type]


def test_public_base_url_trims_surrounding_whitespace() -> None:
    """首尾空白是复制粘贴噪音，意图没有歧义 —— 归一化掉，不为此让部署起不来。
    内部空白则相反（见上一条参数化用例）：那是真的写错了。"""
    assert Settings(**_NO_ENV_FILE, public_base_url=" https://hrbp.example.com ").public_base_url == (
        "https://hrbp.example.com"
    )


def test_public_base_url_accepts_loopback_and_normalizes_a_trailing_slash() -> None:
    """尾斜杠是常见的无害写法，归一化即可 —— 若原样保留会拼出 ``//.well-known``。"""
    assert Settings(**_NO_ENV_FILE, public_base_url="http://localhost:8000/").public_base_url == (
        "http://localhost:8000"
    )


def test_derived_urls_all_come_from_the_same_origin() -> None:
    configured = Settings(**_NO_ENV_FILE, public_base_url="https://hrbp.example.com/")

    assert configured.mcp_resource_url == "https://hrbp.example.com/mcp"
    assert configured.protected_resource_metadata_url == (
        "https://hrbp.example.com/.well-known/oauth-protected-resource"
    )


def test_authorization_servers_must_be_absolute_https() -> None:
    """issuer 与元数据里的 ``issuer`` 逐字节比对是 RFC 9207 的 mix-up 防御前提，
    所以不接受 http，也不接受相对形式。"""
    with pytest.raises(ValidationError, match="MCP_AUTHORIZATION_SERVERS"):
        Settings(**_NO_ENV_FILE, mcp_authorization_servers="http://idp.example.com")  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="MCP_AUTHORIZATION_SERVERS"):
        Settings(**_NO_ENV_FILE, mcp_authorization_servers="idp.example.com")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# HTTP 层：发现端点可达，MCP 出口给出挑战
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"],
)
def test_discovery_document_is_anonymous_across_every_middleware_layer(client: TestClient, path: str) -> None:
    """认证、限流、RBAC 三层必须**都**放行发现端点。

    这条断言之所以存在：它真实失败过。``/.well-known/`` 当时只加进了认证层，请求随后
    被限流层以 403 "Missing user context" 拒掉 —— 而认证层的日志显示"已正常放行"，
    症状指向了错误的那一层。三层现在共用 ``WELL_KNOWN_PREFIX``，本测试是它的守卫。
    """
    response = client.get(path)

    assert response.status_code == 200, f"{path} 被中间件拦下：{response.status_code} {response.text}"
    assert response.json()["resource"] == settings.mcp_resource_url


def test_discovery_document_serves_both_variants_identically(client: TestClient) -> None:
    root = client.get("/.well-known/oauth-protected-resource")
    with_path = client.get("/.well-known/oauth-protected-resource/mcp")

    assert root.status_code == with_path.status_code == 200
    assert root.json() == with_path.json()


def test_mcp_without_credentials_returns_a_discoverable_challenge(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})

    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge == f'Bearer resource_metadata="{settings.protected_resource_metadata_url}"'
    # 冗余放在 body 里，便于人工 curl 排查；内容必须与头部一致。
    assert response.json()["resource_metadata"] == settings.protected_resource_metadata_url


def test_mcp_with_unusable_credentials_reports_invalid_token(client: TestClient) -> None:
    response = client.post("/mcp", headers={"Authorization": "Bearer not-a-jwt"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]
    assert f'resource_metadata="{settings.protected_resource_metadata_url}"' in response.headers["WWW-Authenticate"]


def test_mcp_refuses_a_refresh_token_as_an_access_credential(client: TestClient) -> None:
    from app.access.routes.auth import _create_refresh_token

    response = client.post(
        "/mcp", headers={"Authorization": f"Bearer {_create_refresh_token('u-1', 'tenant-a')}"}, json={}
    )

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_transport_layer_does_not_authorize_tools(client: TestClient) -> None:
    """传输层的职责边界：只回答"这份凭据是否可辨认"，不回答"它能不能调某个工具"。

    凭据可辨认 ⇒ 放行（即使这个角色对任何工具都无权）。越权判定在工具层，由
    ``authorize_tool_call`` 做，两条出口共用 —— 这里断言中间件不会在传输层就替它
    下结论，否则 ``initialize`` / ``tools/list`` 这类不对应任何工具的请求会无法处理。
    """
    response = client.post(
        "/mcp", headers={"Authorization": f"Bearer {_platform_access_token(role='employee')}"}, json={}
    )

    assert response.status_code != 401, "有效凭据被传输层拒绝 —— 越权判定被前移了"


def test_platform_tokens_can_be_switched_off_for_the_mcp_surface(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AS 上线后把这个开关翻成 false，平台令牌必须在 ``/mcp`` 上失效。

    这是 ADR-0002 §4「内部会话令牌在 /mcp 上不被接受」的可执行形式：届时它不再是
    一条只写在文档里的意图，而是一条会红的断言。
    """
    monkeypatch.setattr(settings, "mcp_accepts_platform_tokens", False)

    response = client.post("/mcp", headers={"Authorization": f"Bearer {_platform_access_token()}"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]
