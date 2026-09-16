"""HRBP AI Workbench — centralized configuration."""

from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 允许使用明文 http 的主机。仅限本机开发 —— 见 ``_normalize_public_base_url``。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: ``oauth_issuer`` 的内置默认值（单端口部署：AS 挂在 app 自己身上）。
#: 单独提出来是给一致性校验用的：**"issuer 还是默认值"等价于"没有人有意指向过一个
#: 真实 AS"**。校验不该靠猜"用户有没有配"，而要有一个确定的参照物。
#:
#: 它必须与 ``app_port`` / ``public_base_url`` 的默认值**同源**（都是 8000）：
#: 单端口部署下三者指的是同一个 origin，任何一项单独漂移都会让 iss/aud 比对失败，
#: 而失败只发生在客户端、服务端日志里只留一句 ``malformed``。
_DEFAULT_OAUTH_ISSUER_ORIGIN = "http://localhost:8000"


def _normalize_origin(value: str, *, production: bool, name: str) -> str:
    """校验并规范化一个对外 origin，返回不带尾斜杠的形式。

    为什么值得在启动期就拒绝
    ------------------------
    这类值（``PUBLIC_BASE_URL``、``OAUTH_ISSUER``）会被写进**客户端拿去自动发现**的
    文档里 —— RFC 9728 元数据的 ``resource``、401 挑战头的 ``resource_metadata``、
    RFC 8414 文档的 ``issuer``、以及 access token 必须绑定的 audience。一旦写错，
    失败发生在客户端，表现为"授权流程莫名其妙地走不通"，而本服务的日志里什么都
    看不到：客户端不会把"我读到的 issuer 不是 https"或"URL 里多了一条路径"报回来。

    所以这里宁可启动失败，也不接受"看着差不多"的写法。可接受的只有 origin：
    带路径会让 ``/.well-known/`` 的解析位置改变（RFC 9728 §3.1 与 RFC 8414 §3.1
    都把 well-known 插在 host 与 path 之间），尾斜杠会拼出 ``//.well-known``。

    ``name`` 只影响错误文案 —— 但**不是**细节：多个配置项共用这条校验时，报错必须
    指名道姓说哪一个是错的，否则运维要从一句"must be an origin"去猜是哪个变量。
    """
    raw = value.strip()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if any(character.isspace() for character in raw):
        raise ValueError(f"{name} must not contain whitespace")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{name} must be an absolute URL, for example https://hrbp.example.com")
    if parts.username or parts.password:
        raise ValueError(f"{name} must not embed credentials (user:pass@)")
    if parts.query or parts.fragment:
        raise ValueError(f"{name} must not contain a query string or fragment")
    if parts.path.strip("/"):
        raise ValueError(f"{name} must be an origin only — it must not contain a path component")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"{name} must include a host")
    loopback = host in _LOOPBACK_HOSTS
    if production and (parts.scheme != "https" or loopback):
        raise ValueError(f"{name} must be a public https origin in staging or production")
    if parts.scheme == "http" and not loopback:
        raise ValueError(f"{name} must use https unless the host is loopback (local development)")
    return f"{parts.scheme}://{parts.netloc}"


def _normalize_public_base_url(value: str, *, production: bool) -> str:
    """Resource Server 的对外基址（见 ``_normalize_origin`` 的通用说明）。"""
    return _normalize_origin(value, production=production, name="PUBLIC_BASE_URL")


def _parse_authorization_servers(value: str, *, production: bool) -> tuple[str, ...]:
    """解析逗号分隔的授权服务器 issuer 列表。

    每一项都必须是绝对 URL：issuer 与元数据文档里 ``issuer`` 字段的**逐字节一致**
    是 RFC 9207 mix-up 防御的前提，所以这里不接受相对形式 —— 它无法参与那个比对。

    与 ``_normalize_origin`` 的两点差别，都是**故意**的：

    1. **允许带路径**。issuer 带路径是合法的，RFC 8414 §3.1 规定了此时 well-known
       的位置（插在 host 与 path 之间）。RS 侧取 AS 元数据时按这条规则拼 URL。
       把带路径的 issuer 拒掉会让"AS 挂在 /auth 子路径下"这一常见部署无法配置。
    2. **环回主机允许 http**。理由与 ``_normalize_origin`` 相同：本机联调时 AS 跑在
       ``http://localhost:8001``。生产/预发仍然强制公网 https —— 信任列表里出现一个
       明文地址，等于允许任何能在链路上注入的人伪造一个受信任的 issuer。
    """
    servers: list[str] = []
    for item in (part.strip() for part in value.split(",")):
        if not item:
            continue
        parts = urlsplit(item)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entries must be absolute URLs, got {item!r}")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entry must not carry credentials, query or fragment: {item!r}")
        host = (parts.hostname or "").lower()
        if not host:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entry must include a host: {item!r}")
        loopback = host in _LOOPBACK_HOSTS
        if production and (parts.scheme != "https" or loopback):
            raise ValueError(
                f"MCP_AUTHORIZATION_SERVERS entries must be public https URLs in staging or production: {item!r}"
            )
        if parts.scheme == "http" and not loopback:
            raise ValueError(f"MCP_AUTHORIZATION_SERVERS entry must use https unless the host is loopback: {item!r}")
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
    # 省略则明确表示"尚未配置"，并触发规范定义的 fallback。
    #
    # 它同时是 **Resource Server 的信任列表**：``/mcp`` 只接受 ``iss`` 命中这份列表的
    # 令牌（``app/access/as_tokens.py``）。因此这个字段一旦填写，就是一次安全决策，
    # 不是"顺便多填一个地址"。
    mcp_authorization_servers: str = ""
    # /mcp 是否接受平台自签 access token（网页登录用的那套 HS256 JWT）。
    #
    # 这是**过渡开关，不是一个可长期保留的选项**。AS 上线前，外部 Agent 根本拿不到
    # 令牌；若在这里就拒绝自签凭据，MCP 出口会只剩 401，现有联调全部失效。AS 上线
    # 后应置为 false，使 /mcp 只接受 audience 绑定到 MCP resource 的令牌
    # （ADR-0002 §4）—— 届时把它翻成 false 是一个**有意为之**的动作，会被
    # deployment 记录看到，而不是悄悄发生。
    mcp_accepts_platform_tokens: bool = True

    #: 外部 Agent 出口的总开关（方案 §WP8 的"回滚必须能关闭外部 MCP"）。
    #:
    #: 关掉后 /mcp 返回 **503** 而不是 401。这个选择很关键：401 会让客户端启动
    #: 完整的 OAuth 发现流程（读元数据 → 注册 → 授权），用户被莫名其妙地拉去登录，
    #: 登录完再来一次 401 —— 而真实原因只是"这个功能被关了"。503 说的是"暂时不可用"，
    #: 客户端的正确反应是稍后重试，而不是去重新授权。
    #:
    #: 内部工作台与 /api/* 不受影响：回滚外部接入不该让 HR 同事用不了工作台。
    mcp_external_enabled: bool = True

    # ---- OAuth 2.1 授权服务器（ADR-0002 §6；同仓独立应用，独立进程）----
    # AS 自身的对外标识（issuer，RFC 8414 §2）。
    #
    # 刻意是**独立配置项**，不从 public_base_url 派生。ADR-0002 §5 曾写
    # `issuer = https://<public_base_url>/oauth`，但那个取值隐含两个未验证的假设：
    # (a) AS 与 RS 必须同 host —— 那需要反向代理才成立，而本仓库里没有反代配置；
    # (b) 带路径的 issuer 会把元数据端点推到
    #     `/.well-known/oauth-authorization-server/oauth`（RFC 8414 §3.1 的规则），
    #     而不是 ADR 里写的无后缀路径 —— 即原文那一条本身就与规范不符。
    # issuer 是 AS 的对外身份，与 resource 是 RS 的对外身份，两者是**不同的东西**，
    # 各自显式配置比用一个拼接触发的不一致假设更稳。
    oauth_issuer: str = _DEFAULT_OAUTH_ISSUER_ORIGIN
    # Optional container/service-mesh address used only to fetch this trusted
    # issuer's metadata and JWKS. Token `iss` and public discovery always keep
    # using oauth_issuer; this is transport routing, not another trusted issuer.
    oauth_internal_base_url: str = ""
    # ES256（ECDSA P-256）签名私钥。接受 PEM 原文，或 base64 编码的 PEM
    # （编排层常常破坏多行值，两种都收）。生产/预发必须配置；
    # 开发环境留空会生成**进程级临时密钥**（重启即失效，只影响本地联调）。
    oauth_signing_key_pem: str = ""
    # 仅用于**验签**的旧公钥（多块 PEM 拼接），供密钥轮换的过渡窗口使用：
    # 轮换时新密钥转正、旧公钥留在这里，等在途令牌自然过期后清空。
    oauth_rotated_public_keys_pem: str = ""
    oauth_access_token_ttl_seconds: int = 900
    # 授权码是**一次性且极短命**的：它在浏览器地址栏/Referer 里出现过，60 秒足够
    # 完成一次重定向加一次换令牌，超出这个窗口的收益远小于被截获的代价。
    oauth_authorization_code_ttl_seconds: int = 60
    oauth_refresh_token_ttl_days: int = 30
    # Dynamic Client Registration（RFC 7591）开关，**默认关闭**。
    #
    # ADR-0002 §3 把它降级为"兼容回退（MAY，已弃用）"，主路径是 CIMD 与预注册。
    # 依据是一项对 119 个启用 OAuth 的 MCP 服务器的研究：119/119 至少一项授权缺陷，
    # 96.6% 存在 DCR 相关缺陷。默认关闭意味着"没有实测证据就不开启这条路"，
    # 而不是"为了保险两边都开" —— 后者会让 CIMD 的校验优势被绕过。
    oauth_enable_dynamic_registration: bool = False

    # WorkBuddy 等客户端会优先用私有协议回调（workbuddy://...）。默认**空** ——
    # 自定义 scheme 在设备上可被抢注，放行是需要签字的决定，不是默认值。
    # 理由与缓解见 app/oauth/clients.py 的 allowed_custom_redirect_schemes。
    oauth_custom_redirect_schemes_raw: str = ""
    # DCR 的目标注册上限（单实例每小时的注册请求数）。开启 DCR 时它同时是限速依据：
    # DCR 是未认证端点，没有任何上限就等于给了攻击者一个无限量的客户端注册入口。
    oauth_dynamic_registration_per_hour: int = 20

    # 授权请求未携带 scope 时的**可配置默认**（任务书 2026-09-15 T2 路线 B）。
    #
    # 空串 = 维持既有行为：回退到该客户端注册时的全部 scope（DCR 客户端注册未声明
    # scope 时即全部 5 项，见 app/oauth/clients.py 的回退）。
    #
    # 安全边界（不得绕过，改动前先读这条）：
    # 1. 默认 scope 只决定"这次授权**申请**哪些权限"；一次工具调用能否通过仍由
    #    RS 侧 authorize_tool_call（角色能力 ∩ 令牌 scope ∩ 客户端上限）判定，
    #    effective_scopes = scopes ∩ client_ceiling —— 这道交集没有任何配置能跳过。
    # 2. 写权限（hrb:case:propose）不得经由默认值发放给 DCR/CIMD 注册、且尚未
    #    绑定租户的客户端：见 authorize 路由里的过滤。同意页仍会逐项列出所申请的
    #    scope，用户在那一步看得到并可以拒绝。
    # 3. 配置值只接受本服务词表内的 scope（校验见 validate_oauth_default_scope），
    #    拼错的取值在启动期即报错，而不是静默变成"什么都没配"。
    oauth_default_scope_raw: str = ""
    # CIMD 文档在库里的保鲜期。超出后重新抓取一次。
    #
    # 不做"每次授权都重取"：那会让授权端点的延迟取决于客户端域名的响应速度，也会把
    # 一个未认证端点变成"可以反复让我去访问某个 URL"的放大器。也不做"永不重取"：
    # 客户端会演进它的 redirect_uris，永不更新会让库里停在一个谁都不再维护的旧值上。
    oauth_cimd_cache_ttl_seconds: int = 86400

    # CIMD 抓取允许**解析到私网/环回地址**的主机名清单（逗号分隔，按主机名精确匹配）。
    #
    # 默认空，且**在 production / staging 下设置它会让进程启动失败**（见
    # ``validate_oauth_cimd_fetch_reach``）。CIMD 的抓取目标完全由未认证的调用方决定
    # （``client_id`` 就是这个 URL），所以这个开关打开就等于把 AS 变成一个"替我访问
    # 内网"的工具 —— 它是**开发与本地验收专用**，不是部署选项。
    #
    # 为什么仍然要提供它而不是让测试去替换掉抓取函数：CIMD 最要紧的两条不变式
    # （文档自证、缓存回退）只有在**真的发出一趟 HTTPS 请求**时才会被执行。替换掉抓取
    # 函数等于把这两条一起测掉了 —— 而它们正是这个模块存在的理由。
    #
    # 匹配按**主机名**而不是解析结果：若按解析结果放宽，白名单里写一个 127.0.0.1
    # 就等于放开了所有指向 127.0.0.1 的名字，而攻击者恰好可以注册这样一个名字。
    oauth_cimd_allowed_private_hosts_raw: str = ""

    # CIMD 抓取时的 CA bundle 路径（PEM）。留空走系统/默认信任库。
    #
    # 这一项**不是**测试专用：企业私有 PKI 签发的客户端文档服务器是真实场景，
    # 而它的正确做法就是把根证书交给 AS，而不是关掉校验。
    oauth_cimd_ca_bundle: str = ""
    # 预注册客户端（运维声明的企业内部固定客户端），JSON 数组。每个元素形如：
    #   {"client_id": "...", "client_name": "...",
    #    "redirect_uris": ["http://127.0.0.1/callback"], "scope": "hrb:policy:read"}
    # AS 启动时把它同步进数据库 —— 配置是**声明**，数据库是运行时的**唯一查询入口**，
    # 三条注册路径（预注册/CIMD/DCR）因此共用同一条解析路径。
    oauth_pre_registered_clients: str = "[]"

    guardrail_pii_detection_enabled: bool = True
    guardrail_prompt_injection_enabled: bool = True
    guardrail_factuality_check_enabled: bool = False
    guardrail_toxicity_detection_enabled: bool = True
    guardrail_confidence_threshold: float = 0.65

    rate_limit_tenant_per_minute: int = 60
    rate_limit_user_per_minute: int = 30
    rate_limit_fail_open: bool = False

    # 外部 Agent 的限流维度（方案 §WP3）。``/mcp`` 不能复用 tenant/user 两个桶：
    # 一个 Agent 可以在几秒内打出上百次调用，而它和为它授权的**人**共享同一个
    # user 桶 —— 结果是用户被自己的客户端挤下线，而按租户看不出是哪个客户端干的。
    # MCP_ACCEPTS_PLATFORM_TOKENS 下平台令牌没有 client/installation 维度，
    # 只能落到 user 桶，这一点在代码里按维度缺失处理，不是静默跳过。
    mcp_rate_limit_user_per_minute: int = 120
    mcp_rate_limit_client_per_minute: int = 600
    mcp_rate_limit_installation_per_minute: int = 300

    # OAuth 匿名端点（authorize / token / register / introspect）按 IP 与 client_id
    # 分别计数。它们**早于任何身份**被调用，因此只能按 IP 兜底；按 client_id 是
    # 为了在已知客户端时能给出比 IP 更准的桶（同一 NAT 后可能有多个用户）。
    oauth_authorize_per_minute_per_ip: int = 30
    oauth_token_per_minute_per_ip: int = 60
    oauth_introspect_per_minute_per_ip: int = 120
    oauth_token_per_minute_per_client: int = 300

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
        _ = _parse_authorization_servers(self.mcp_authorization_servers, production=self.is_production)
        return self

    @property
    def oauth_custom_redirect_schemes(self) -> frozenset[str]:
        """逗号分隔的自定义 scheme 清单，统一小写（scheme 比较大小写无关）。"""
        return frozenset(
            item.strip().lower() for item in self.oauth_custom_redirect_schemes_raw.split(",") if item.strip()
        )

    @property
    def oauth_cimd_allowed_private_hosts(self) -> frozenset[str]:
        """CIMD 抓取允许命中私网地址的主机名，统一小写（主机名比较大小写无关）。"""
        return frozenset(
            item.strip().lower() for item in self.oauth_cimd_allowed_private_hosts_raw.split(",") if item.strip()
        )

    @property
    def oauth_default_scope(self) -> "frozenset[str]":
        """授权请求未携带 scope 时的默认申请范围（空 = 维持既有回退，见字段注释）。

        返回原始字符串集合（词表值）；调用方（authorize 路由）会再与客户端
        注册范围做交集。解析用 ``parse_scopes`` 的同款丢弃语义：
        认不出的取值不抛错 —— 启动期的 ``validate_oauth_default_scope``
        已经挡住了"拼错还不知道"的情形。
        """
        from app.access.scopes import parse_scopes

        return parse_scopes(self.oauth_default_scope_raw.replace(",", " "))

    @model_validator(mode="after")
    def validate_oauth_default_scope(self) -> "Settings":
        """默认 scope 只接受本服务词表内的取值 —— 拼错要在启动期炸，不是静默为空。

        静默为空的后果是"以为配了查询版、实际维持全集回退"，两种读法南辕北辙；
        这种配置漂移在授权类系统里属于最贵的那类事故。
        """
        if self.oauth_default_scope_raw.strip():
            from app.access.scopes import Scope

            raw_items = [p for p in self.oauth_default_scope_raw.replace(",", " ").split(" ") if p]
            known = {scope.value for scope in Scope}
            unknown = [item for item in raw_items if item not in known]
            if unknown:
                raise ValueError(
                    f"OAUTH_DEFAULT_SCOPE contains unknown scope(s): {', '.join(unknown)}; "
                    f"known scopes: {', '.join(sorted(known))}"
                )
        return self

    @model_validator(mode="after")
    def validate_oauth_authorization_server(self) -> "Settings":
        """规范化 AS 的 issuer，并让"生产没配签名密钥"在启动期就失败。

        签名密钥的**格式**不在这里校验（那需要 import cryptography，而密钥的
        解析逻辑属于 AS 自己的模块）。这里只挡住最贵的一种错误：生产环境忘了配
        密钥，然后服务正常启动、正常 200，直到第一个客户走到换令牌那一步才发现
        签不出东西 —— 那时错误现场在客户端，而服务端日志里只有一次普通请求。
        AS 启动时会主动加载一次密钥，把格式错误也拉到启动期（fail fast）。
        """
        self.oauth_issuer = _normalize_origin(self.oauth_issuer, production=self.is_production, name="OAUTH_ISSUER")
        if self.is_production and not self.oauth_signing_key_pem.strip():
            raise ValueError("OAUTH_SIGNING_KEY_PEM must be configured in staging or production")
        return self

    @model_validator(mode="after")
    def validate_mcp_auth_can_accept_something(self) -> "Settings":
        """挡住"配置自相矛盾导致 /mcp 没有任何凭据能通过"的两种写法。

        为什么值得在启动期拒绝，而不是等第一个客户端撞 401
        ----------------------------------------------------
        2026-09-16 实测：`.env` 里指了 AS（``OAUTH_ISSUER=http://localhost:8002``）
        但 ``MCP_AUTHORIZATION_SERVERS`` 留空，结果是**所有**外部令牌被判
        ``malformed`` —— 而日志里只有 ``mcp_token_rejected reason=malformed``。
        ``malformed`` 把"签名不过 / 过期 / audience 不符 / issuer 不在信任列表"
        揉成一个值，运维要从这里逆推出"其实少配了一个环境变量"，成本极高。
        这两条矛盾在启动期是**确定的**，没有理由留到请求期才暴露。

        刻意检查"矛盾"而不是"必须填"：单端口部署、仅平台令牌部署都是合法的，
        无脑要求非空会把它们一起打死。触发条件都带 ``mcp_external_enabled``，
        给"就是要关掉外部 MCP"的部署留出口。
        """
        if not self.mcp_external_enabled:
            return self

        trusted = self.authorization_servers
        if trusted:
            return self

        # 矛盾一：issuer 被有意指向了一个真实 AS，但信任列表是空的。
        # "我在某个地方跑着 AS" + "我不信任任何 AS" 不可能同时成立。
        if self.oauth_issuer != _DEFAULT_OAUTH_ISSUER_ORIGIN:
            raise ValueError(
                f"OAUTH_ISSUER is set to {self.oauth_issuer!r} but MCP_AUTHORIZATION_SERVERS is empty: "
                "the resource server would trust no issuer, so every external token would be rejected as "
                "'malformed'. Set MCP_AUTHORIZATION_SERVERS to the same issuer "
                f"(for example MCP_AUTHORIZATION_SERVERS={self.oauth_issuer}), or set MCP_EXTERNAL_ENABLED=false."
            )

        # 矛盾二：外部令牌无人可信、平台令牌又被策略关闭 → /mcp 无任何可用凭据。
        if not self.mcp_accepts_platform_tokens:
            raise ValueError(
                "MCP_AUTHORIZATION_SERVERS is empty and MCP_ACCEPTS_PLATFORM_TOKENS is false: no credential "
                "type could ever be accepted, so /mcp is unreachable. Configure MCP_AUTHORIZATION_SERVERS, "
                "or set MCP_ACCEPTS_PLATFORM_TOKENS=true, or set MCP_EXTERNAL_ENABLED=false."
            )
        return self

    @model_validator(mode="after")
    def validate_oauth_cimd_fetch_reach(self) -> "Settings":
        """挡住"在生产环境把 SSRF 守卫关掉"。

        这里选择**拒绝启动**而不是"忽略配置并记一条日志"。理由：设置了这个值的运维
        确信自己已经打开了某项能力，而静默忽略会让他在事故之后才发现它从未生效 ——
        那时"我们配了但没生效"与"我们根本没配"在日志里长得一样。启动失败没有这个歧义。

        错误信息里带上该设什么，而不是只说"不允许"：这类开关的正常用途是本地验收，
        而本地验收应该跑在 development 下。
        """
        if self.is_production and self.oauth_cimd_allowed_private_hosts:
            raise ValueError(
                "OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS must not be set in staging or production: it disables the "
                "SSRF guard on an unauthenticated endpoint. Use APP_ENV=development for local acceptance runs."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def authorization_servers(self) -> tuple[str, ...]:
        """RFC 9728 ``authorization_servers`` 的取值；空元组表示尚未配置 AS。

        同一个取值被两处使用：发现面把它写进元数据文档，RS 侧把它当**信任列表**
        （``app/access/as_tokens.py`` 的 ``accepts_issuer``）—— 两处必须是同一份，
        否则会出现"告诉客户端去 A 拿令牌，而自己只认 B"的死锁。
        """
        return _parse_authorization_servers(self.mcp_authorization_servers, production=self.is_production)

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
