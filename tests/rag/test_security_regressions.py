from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError
from starlette.requests import Request

from app.access.middleware.tenant import require_tenant_id
from app.access.routes import auth
from app.config.settings import Settings
from app.guardrails.output_guard import OutputGuardrail
from app.oauth.keys import generate_signing_key
from app.shared.errors import AuthError

# A real 32-byte master key for hermetic production Settings construction
# (enforced since the connector backbone added credential encryption).
REAL_MASTER_KEY = __import__("base64").urlsafe_b64encode(b"hermetic-master-key-32-bytes-ok!"[:32])

#: 生产必须配置 AS 的签名密钥。这里**现场生成**一把 P-256 私钥，而不是写一个固定值 ——
#: 把一把私钥（哪怕只是测试用的）提交进仓库是这一轮最不该发生的事：它会被扫描器
#: 命中、被后来的人复制到真实环境，而删除它还要改写历史。
OAUTH_SIGNING_PEM = generate_signing_key().private_pem.decode()


def _production_settings(**overrides: object) -> Settings:
    """构造一个 hermetic 的生产配置，只覆盖调用方关心的项。

    这里是"生产必需配置"的**唯一集合**。新增一项生产约束时，把它的合法默认值补在
    这里 —— 否则每个用到它的测试都会以"与它无关的那一项"失败。本轮 ``PUBLIC_BASE_URL``
    就是这么暴露出来的：``test_production_accepts_strong_jwt_secret`` 断言的是 JWT
    secret，却因为没给对外基址而报错，症状指向了错误的地方。

    ``public_base_url`` 必须显式给成 https 公网 origin（不能沿用默认的 localhost）：
    它会出现在 OAuth 元数据与令牌 audience 里，是**对外身份**；而 ``vector_db_host``
    一类是**内部连接地址**，localhost 在生产是正常的。两者性质不同，所以后者不受
    这条约束，前者受。

    ``oauth_issuer`` 与 ``mcp_authorization_servers`` 同理受这条约束：前者是授权服务器
    的对外身份（写进 RFC 8414 元数据），后者是 Resource Server 的**信任列表** ——
    里面出现一个明文地址，等于允许任何能在链路上改写响应的人伪造一个受信任的 issuer。
    """
    base: dict[str, object] = {
        "_env_file": None,  # hermetic: 不继承本机 .env（例如 VECTOR_DB_PORT）
        "app_env": "production",
        "jwt_secret": "a" * 32,
        "connector_master_key": REAL_MASTER_KEY,
        "llm_api_key": "configured-llm-key",
        "embedding_api_key": "configured-embedding-key",
        "embedding_base_url": "https://embedding.example/v1",
        "minio_access_key": "configured-minio-key",
        "minio_secret_key": "configured-minio-secret",
        "public_base_url": "https://hrbp.example.com",
        "oauth_issuer": "https://as.hrbp.example.com",
        "mcp_authorization_servers": "https://as.hrbp.example.com",
        "oauth_signing_key_pem": OAUTH_SIGNING_PEM,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def test_database_login_returns_tokens_on_valid_credentials(monkeypatch) -> None:
    user = SimpleNamespace(
        id="user-1",
        email="hr@example.com",
        role="hr_manager",
        tenant_id="tenant-1",
        hashed_password="hash",
    )

    async def db_available() -> bool:
        return True

    async def get_user(_self, email: str):
        assert email == user.email
        return user

    async def fake_session():
        yield SimpleNamespace()

    auth._LOGIN_ATTEMPTS.clear()
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/auth/login", "headers": [], "client": ("127.0.0.1", 0)}
    )

    monkeypatch.setattr(auth, "_check_db_available", db_available)
    monkeypatch.setattr("app.data.database.get_db_session", fake_session)
    monkeypatch.setattr("app.data.repositories.user_repo.UserRepository.get_by_email", get_user)
    monkeypatch.setattr("bcrypt.checkpw", lambda *_args: True)

    result = await auth.login(auth.LoginBody(email=user.email, password="secret"), request)

    assert result.access_token
    assert result.refresh_token


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(PydanticValidationError, match="JWT_SECRET"):
        Settings(
            app_env="production",
            jwt_secret="change-me-in-production",
            connector_master_key=REAL_MASTER_KEY,
        )


def test_production_accepts_strong_jwt_secret() -> None:
    configured = _production_settings(
        jwt_secret="a" * 32,
        vector_db_port=19530,
    )
    assert configured.is_production
    assert configured.milvus_endpoint == "http://localhost:19530"


def test_production_rejects_loopback_public_base_url() -> None:
    """生产环境的对外基址不能是环回地址。

    这个值会出现在 RFC 9728 元数据的 ``resource`` 与 401 挑战的 ``resource_metadata``
    里，被外部客户端用来发现授权服务器。指向 localhost 等于对外声明一个任何客户端都
    用不上的身份 —— 而且是**静默**的：服务照常启动、照常返回 200，只有客户端在别处
    莫名其妙地失败。所以宁可在启动期拒绝。
    """
    with pytest.raises(PydanticValidationError, match="PUBLIC_BASE_URL"):
        _production_settings(public_base_url="http://localhost:8000")


def test_production_rejects_plain_http_public_base_url() -> None:
    """公网 origin 必须 https —— 明文基址会让 issuer 与令牌 audience 可被中间人改写。"""
    with pytest.raises(PydanticValidationError, match="PUBLIC_BASE_URL"):
        _production_settings(public_base_url="http://hrbp.example.com")


def test_production_rejects_loopback_or_plain_http_oauth_issuer() -> None:
    """AS 的 issuer 是它对外的身份，与 ``PUBLIC_BASE_URL`` 同类。

    生产留 localhost 会让 RFC 8414 元数据声明一个外部客户端永远够不着的 issuer，
    而且服务照常启动、照常返回 200 —— 失败只发生在客户端。
    """
    with pytest.raises(PydanticValidationError, match="OAUTH_ISSUER"):
        _production_settings(oauth_issuer="http://localhost:8001")
    with pytest.raises(PydanticValidationError, match="OAUTH_ISSUER"):
        _production_settings(oauth_issuer="http://as.hrbp.example.com")


def test_production_rejects_an_unsafe_authorization_server() -> None:
    """信任列表里出现明文地址 = 允许任何能在链路上改写响应的人伪造一个受信任的 issuer。"""
    with pytest.raises(PydanticValidationError, match="MCP_AUTHORIZATION_SERVERS"):
        _production_settings(mcp_authorization_servers="http://as.hrbp.example.com")

    with pytest.raises(PydanticValidationError, match="MCP_AUTHORIZATION_SERVERS"):
        _production_settings(mcp_authorization_servers="https://as.hrbp.example.com,http://localhost:8001")


def test_development_trusts_a_loopback_authorization_server() -> None:
    """本机联调：AS 跑在 ``http://localhost:8001``，RS 必须能把它写进信任列表。

    没有这条豁免，e2e 只能靠改生产约束才能跑起来 —— 那会让"生产与开发配置不同"
    这件事从一处显式例外变成一条隐形的分支。
    """
    configured = Settings(
        _env_file=None, oauth_issuer="http://localhost:8001", mcp_authorization_servers="http://localhost:8001"
    )  # type: ignore[arg-type]

    assert configured.authorization_servers == ("http://localhost:8001",)


def test_authorization_server_issuer_may_carry_a_path() -> None:
    """issuer 带路径是合法的（RFC 8414 §3.1 规定了此时 well-known 的位置）。

    把带路径的 issuer 拒掉，会让"AS 挂在 /auth 子路径下"这一常见部署无法配置 ——
    而那正是 ``_parse_authorization_servers`` 与 ``_normalize_public_base_url``
    刻意不同的地方。
    """
    configured = Settings(_env_file=None, mcp_authorization_servers="https://id.example.com/auth")  # type: ignore[arg-type]

    assert configured.authorization_servers == ("https://id.example.com/auth",)


def test_production_rejects_missing_llm_or_embedding_configuration() -> None:
    with pytest.raises(PydanticValidationError, match="LLM_API_KEY"):
        Settings(
            app_env="production",
            jwt_secret="a" * 32,
            connector_master_key=REAL_MASTER_KEY,
            llm_api_key="change-me",
            embedding_api_key="",
            embedding_base_url="",
            minio_access_key="configured-minio-key",
            minio_secret_key="configured-minio-secret",
        )


def test_request_without_tenant_context_fails_closed() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    with pytest.raises(AuthError, match="Tenant context is required"):
        require_tenant_id(request)

    request.state.tenant_id = "tenant-1"
    assert require_tenant_id(request) == "tenant-1"


@pytest.mark.asyncio
async def test_output_guard_desensitizes_pii() -> None:
    guard = OutputGuardrail()
    processed, flags = await guard.check("手机号 13812345678，身份证号 110105199001011234", ["pii_detection"], [])
    assert flags["pii_detected"] is True
    assert "[phone_已脱敏]" in processed or "[id_card_已脱敏]" in processed
