"""授权服务器的持久化模型。

为什么这些表**没有** RLS（对仓库"每张表都有 tenant_id"约定的有意例外）
----------------------------------------------------------------------
RLS 的前提是查询前会话里已经设好 ``app.tenant_id``。但 OAuth 端点的处境恰好相反：
AS 查找客户端、授权码、refresh token 的时候，**正是还不知道租户是谁**的时候 ——
租户要从查出来的那条记录里读。策略写成 ``tenant_id = current_setting('app.tenant_id')``
在没有上下文时恒为 NULL 比较，于是每一次查找都返回零行，授权流程从第一步就死。

可以退让成"没有上下文就放行"，但那样的策略不是隔离，只是把 RLS 写在名字里。
所以这些表不启用 RLS，保护它们的机制是**不可猜的凭据本身**：

- ``oauth_clients``：客户端元数据，``client_id`` 本就是公开信息；
- ``oauth_authorization_codes`` / ``oauth_tokens``：只存 **SHA-256 哈希**，
  原始值从不落库（沿用 ``oauth_nonces.nonce_sha256`` 的既有做法）。访问路径永远是
  "按哈希精确命中"，不存在"扫描一批行"的场景，因此 RLS 拦不住的那类越权
  （忘了写 WHERE 的全表查询）在这里没有对应入口。

``tenant_id`` / ``user_id`` 仍然作为**普通列**保留：审计与运营查询需要它们，
只是不承担隔离职责。

access token 为什么不落库
-------------------------
它是 ES256 签名的 JWT，RS 用 JWKS 本地验签，不需要问 AS。只有**撤销**需要落库 ——
见 ``oauth_revoked_tokens``。
"""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TimestampMixin

#: ``oauth_clients.registration_source`` 的取值。审计要能分辨"这个客户端是谁放进来的"：
#: 预注册（运维配置）、CIMD（客户端自证 URL）、DCR（自助注册，ADR-0002 §3 的兼容回退）。
REGISTRATION_SOURCES = ("pre_registered", "cimd", "dcr")

#: ``oauth_revoked_tokens.kind`` 的取值。撤销分两个粒度：
#: - ``jti``：单个 access token；
#: - ``family``：一条 refresh 轮换链（撤销"这次授权会话"，把由它派生出的所有
#:   access token 一并作废）。
#: refresh token 自身的撤销不改这张表，它落在 ``oauth_tokens.revoked_at`` 上。
REVOCATION_KIND_JTI = "jti"
REVOCATION_KIND_FAMILY = "family"

#: 客户端归属租户的缺省值。``oauth_clients`` 不受 RLS（见模块头部），所以这个列
#: **不承担隔离职责**，它的用途是回答"授权这个客户端时，该去哪个租户的用户目录里
#: 找人"：AS 登录按客户端的租户进入对应用户表的 RLS 上下文（``app/oauth/identity.py``）。
#: 自助注册路径（DCR / CIMD）一律落回这个缺省值 —— 让 self-service 注册自选租户
#: 等于把跨租户入口交给匿名请求。
DEFAULT_TENANT = "default"
UNBOUND_TENANT = "__unbound__"


class OAuthClient(Base, TimestampMixin):
    """一个已注册的 OAuth 客户端。"""

    __tablename__ = "oauth_clients"

    #: RFC 7591 的 ``client_id``。CIMD 客户端的它是**一个 HTTPS URL**（可达数百字符），
    #: DCR/预注册的它是随机串 —— 用同一个列宽容纳两者。
    client_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: RFC 7591 的 ``redirect_uris``。**精确串匹配**的比对基准，不做前缀或通配。
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    response_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: 允许申请的 scope（空格分隔，RFC 6749 §3.3 的形式）。空串表示不允许任何业务 scope。
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 首版只接受 ``none``（public client + PKCE）。见 ``clients.py`` 的说明。
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    #: ``REGISTRATION_SOURCES`` 之一。
    registration_source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: CIMD 客户端的文档地址（与 client_id 逐字节一致）。非 CIMD 为 NULL。
    metadata_document_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 客户端归属的租户。预注册客户端由运维在配置里声明（``OAUTH_PRE_REGISTERED_CLIENTS``
    #: 条目的 ``tenant_id`` 键）；DCR / CIMD 先落 ``UNBOUND_TENANT``，由首次授权登录的
    #: 租户认领（``bind_dynamic_client_tenant``）。这个值决定 AS 登录去哪个租户的
    #: 用户目录里找人，**不能**由注册文档自带。
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_TENANT, server_default=DEFAULT_TENANT
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")

    __table_args__ = (Index("ix_oauth_clients_source", "registration_source"),)

    def __repr__(self) -> str:
        return f"<OAuthClient client_id={self.client_id!r} source={self.registration_source}>"


class OAuthAuthorizationCode(Base, TimestampMixin):
    """一次性、极短命的授权码。

    只存哈希：授权码在浏览器地址栏、Referer 与客户端日志里都出现过，落库时再留一份
    明文等于把它们集中到一个地方。
    """

    __tablename__ = "oauth_authorization_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: 换令牌时必须**逐字节相同**的 redirect_uri（RFC 6749 §4.1.3）。
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: RFC 8707 的 resource，最终要成为 access token 的 audience。
    resource: Mapped[str] = mapped_column(Text, nullable=False, default="")
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(16), nullable=False, default="S256")
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 非空即已兑换。重放检测靠它 —— 不是靠"删掉记录"（删了就分不清"没用过"与"被重放"）。
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_oauth_codes_expires_at", "expires_at"),
        Index("ix_oauth_codes_client", "client_id"),
    )


class OAuthToken(Base, TimestampMixin):
    """一个 refresh token。access token 是无状态 JWT，不在这张表里。"""

    __tablename__ = "oauth_tokens"

    #: 令牌自身的 ID（RFC 7519 的 ``jti``），同时也是行主键。
    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: 一条轮换链的 ID。重用检测与"撤销整个会话"都以它为粒度。
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 链上的前一个令牌。留着是为了让"某条链上发生了什么"可回溯。
    parent_jti: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 非空即已用于换过一次令牌。**再次出现**就是重用信号（RFC 6819 §5.2.2.3）。
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 撤销原因（``family_reuse_detected`` / ``user_revoked`` / …）。审计要能回答
    #: "这条链为什么断了"，而不是只有一个时间戳。
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_oauth_tokens_family", "family_id"),
        Index("ix_oauth_tokens_client", "client_id"),
        Index("ix_oauth_tokens_expires_at", "expires_at"),
    )


class OAuthClientBlock(Base, TimestampMixin):
    """Tenant-local deny state for an OAuth client.

    A shared client identifier may be used by more than one tenant, therefore a
    tenant administrator must never disable the client globally.
    """

    __tablename__ = "oauth_client_blocks"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    blocked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (Index("ix_oauth_client_blocks_client", "client_id"),)


class OAuthRevokedToken(Base, TimestampMixin):
    """被撤销的令牌标识。

    access token 是自包含的 JWT —— 签出去之后 AS 手里没有它的记录，唯一的撤销手段
    就是把它的 ``jti``（或它所属的 ``family_id``）记到这里，让 RS 在验签之后多查一次。
    这是"无状态令牌 + 真撤销"之间的标准折中，代价是 RS 每个请求多一次索引命中。
    """

    __tablename__ = "oauth_revoked_tokens"

    #: ``REVOCATION_KIND_JTI`` 或 ``REVOCATION_KIND_FAMILY``。
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 被撤销令牌**自身的**过期时间。过了这个时刻，这行就再也没有存在意义
    #: （令牌本来就无效了），可以清理 —— 于是这张表的大小与"当前在途令牌数"成正比，
    #: 而不是与历史撤销总数成正比。
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")

    __table_args__ = (Index("ix_oauth_revoked_expires_at", "expires_at"),)
