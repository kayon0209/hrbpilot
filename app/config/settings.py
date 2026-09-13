"""HRBP AI Workbench — centralized configuration."""

from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 允许使用明文 http 的主机。仅限本机开发 —— 见 ``_normalize_public_base_url``。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _normalize_public_base_url(value: str, *, production: bool) -> str:
    """校验并规范化对外基址，返回不带尾斜杠的 origin。

    为什么值得在启动期就拒绝
    ------------------------
    这个值会派生三项对外契约：RFC 9728 元数据里的 ``resource``、401 挑战头里的
    ``resource_metadata``、以及将来 access token 必须绑定的 audience。它们被
    客户端用于**自动发现**授权服务器 —— 也就是说，一旦写错，失败发生在客户端，
    表现为"授权流程莫名其妙地走不通"，而本服务的日志里什么也看不到。
    客户端不会把"我读到的 issuer 不是 https"或"URL 里多了一条路径"报回来。

    所以这里宁可启动失败，也不接受"看着差不多"的写法。可接受的只有 origin：
    带路径会让 ``/.well-known/`` 的解析位置改变（RFC 9728 §3.1 明确把
    well-known 插在 host 与 path 之间），尾斜杠会拼出 ``//.well-known``。
    """
    raw = value.strip()
    if not raw:
        raise ValueError("PUBLIC_BASE_URL must not be empty")
    if any(character.isspace() for character in raw):
        raise ValueError("PUBLIC_BASE_URL must not contain whitespace")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("PUBLIC_BASE_URL must be an absolute URL, for example https://hrbp.example.com")
    if parts.username or parts.password:
        raise ValueError("PUBLIC_BASE_URL must not embed credentials (user:pass@)")
    if parts.query or parts.fragment:
        raise ValueError("PUBLIC_BASE_URL must not contain a query string or fragment")
    if parts.path.strip("/"):
        raise ValueError("PUBLIC_BASE_URL must be an origin only — it must not contain a path component")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("PUBLIC_BASE_URL must include a host")
    loopback = host in _LOOPBACK_HOSTS
    if production and (parts.scheme != "https" or loopback):
        raise ValueError("PUBLIC_BASE_URL must be a public https origin in staging or production")
    if parts.scheme == "http" and not loopback:
        raise ValueError("PUBLIC_BASE_URL must use https unless the host is loopback (local development)")
    return f"{parts.scheme}://{parts.netloc}"


def _parse_authorization_servers(value: str) -> tuple[str, ...]:
    """解析逗号分隔的授权服务器 issuer 列表。

    每一项都必须是 https 绝对 URL：issuer 与元数据文档里 ``issuer`` 字段的
    **逐字节一致**是 RFC 9207 mix-up 防御的前提，所以这里不接受 http，
    也不接受相对形式 —— 它们都无法参与那个比对。
    """
    servers: list[str] = []
    for item in (part.strip() for part in value.split(",")):
        if not item:
            continue
        parts = urlsplit(item)
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entries must be absolute https URLs, got {item!r}")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entry must not carry credentials, query or fragment: {item!r}")
        servers.append(item.rstrip("/"))
    return tuple(servers)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    app_name: str = "hrbp-ai-workbench"
    app_env: str = "development"
    app_port: int = 8000
    app_debug: bool = False

    database_url: str = "postgresql+asyncpg://hrbp:hrbp_password@localhost:5432/hrbp_workbench"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    redis_url: str = "redis://localhost:6379/0"
    redis_session_ttl: int = 900

    jwt_secret: str = "change-me-in-production"
    # Envelope-encryption master key for connector credentials (base64 of 32
    # bytes). The default only works in development; production/staging must
    # set a real key — enforced in validate_environment.
    connector_master_key: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    jwt_access_expires_minutes: int = 15
    jwt_refresh_expires_days: int = 7
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "hrbp-ai-workbench"
    jwt_audience: str = "hrbp-ai-workbench"

    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: str = "change-me"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.3

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    embedding_provider: str = "openai_compatible"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_dimension: int = 1024
    embedding_device: str = "cpu"

    vector_db_host: str = "localhost"
    vector_db_port: int = 19530
    vector_db_type: str = "milvus"
    milvus_uri: str = ""
    milvus_collection: str = "hrbp_chunks"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "hrbp-workbench"
    minio_secure: bool = False

    rrf_k: int = 60
    dense_top_k: int = 20
    sparse_top_k: int = 20

    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    # ---- MCP OAuth Resource Server（ADR-0002 §5）----
    # 服务对外的规范基址。OAuth 的 issuer / resource / audience 都是 URL，而本仓库
    # 此前没有任何地方表达"这个服务对外是什么地址" —— 这是接入外部 Agent 的第一道
    # 前置，不是配置细节。
    #
    # 刻意**不**从 Host / X-Forwarded-Host 推导：那是客户端可控输入，用它生成
    # issuer 是典型的可被投毒的设计。基址只能来自配置。
    public_base_url: str = "http://localhost:8000"
    # 逗号分隔的授权服务器 issuer。为空时 RFC 9728 元数据**省略**
    # `authorization_servers` 字段，而不是返回空数组 —— 空数组的语义规范未定义，
    # 省略则明确表示"尚未配置"，并触发规范定义的 fallback。AS 上线后填入。
    mcp_authorization_servers: str = ""
    # /mcp 是否接受平台自签 access token（网页登录用的那套 HS256 JWT）。
    #
    # 这是**过渡开关，不是一个可长期保留的选项**。AS 上线前，外部 Agent 根本拿不到
    # 令牌；若在这里就拒绝自签凭据，MCP 出口会只剩 401，现有联调全部失效。AS 上线
    # 后应置为 false，使 /mcp 只接受 audience 绑定到 MCP resource 的令牌
    # （ADR-0002 §4）—— 届时把它翻成 false 是一个**有意为之**的动作，会被
    # deployment 记录看到，而不是悄悄发生。
    mcp_accepts_platform_tokens: bool = True

    guardrail_pii_detection_enabled: bool = True
    guardrail_prompt_injection_enabled: bool = True
    guardrail_factuality_check_enabled: bool = False
    guardrail_toxicity_detection_enabled: bool = True
    guardrail_confidence_threshold: float = 0.65

    rate_limit_tenant_per_minute: int = 60
    rate_limit_user_per_minute: int = 30
    rate_limit_fail_open: bool = False

    enable_dev_users: bool = False

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend_url: str = "redis://localhost:6379/2"

    log_level: str = "info"
    log_format: str = "json"

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.app_env not in {"development", "staging", "production"}:
            raise ValueError("APP_ENV must be one of development, staging, production")
        if self.app_env in {"staging", "production"}:
            if self.jwt_secret == "change-me-in-production" or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be a non-default value of at least 32 characters in production or staging"
                )
            if self.connector_master_key == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=":
                raise ValueError("CONNECTOR_MASTER_KEY must be a real 32-byte base64 key in production or staging")
            if not self.llm_api_key or self.llm_api_key == "change-me":
                raise ValueError("LLM_API_KEY must be configured in production or staging")
            if not self.effective_embedding_api_key or not self.embedding_base_url:
                raise ValueError("EMBEDDING_API_KEY and EMBEDDING_BASE_URL must be configured in production or staging")
            if self.enable_dev_users:
                raise ValueError("ENABLE_DEV_USERS must be false outside development")
            if (
                not self.minio_access_key
                or not self.minio_secret_key
                or self.minio_access_key == "minioadmin"
                or self.minio_secret_key == "minioadmin"
            ):
                raise ValueError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be configured in production or staging")
        return self

    @model_validator(mode="after")
    def validate_mcp_resource_server(self) -> "Settings":
        """规范化对外基址，并让授权服务器列表在启动期就暴露畸形取值。

        规范化结果**写回**字段（而不是每次读属性时再算一遍）：下游要在 401 挑战头、
        元数据文档与 audience 比对三处使用它，三处必须逐字节一致，重新解析就是给
        "三处不一致"留口子。
        """
        self.public_base_url = _normalize_public_base_url(self.public_base_url, production=self.is_production)
        _ = _parse_authorization_servers(self.mcp_authorization_servers)
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def authorization_servers(self) -> tuple[str, ...]:
        """RFC 9728 ``authorization_servers`` 的取值；空元组表示尚未配置 AS。"""
        return _parse_authorization_servers(self.mcp_authorization_servers)

    @property
    def mcp_resource_url(self) -> str:
        """MCP 端点的规范资源标识符，也是 access token 必须绑定的 audience（RFC 8707）。"""
        return f"{self.public_base_url}/mcp"

    @property
    def protected_resource_metadata_url(self) -> str:
        """401 挑战里 ``resource_metadata`` 参数的值（RFC 9728 §5.1）。"""
        return f"{self.public_base_url}/.well-known/oauth-protected-resource"

    @property
    def is_production(self) -> bool:
        return self.app_env in {"production", "staging"}

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @property
    def milvus_endpoint(self) -> str:
        if self.milvus_uri:
            return self.milvus_uri
        return "http://" + self.vector_db_host + ":" + str(self.vector_db_port)


settings = Settings()
