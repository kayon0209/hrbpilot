"""客户端注册：元数据校验、``redirect_uri`` 匹配与持久化。

三条注册路径共用**同一套**校验
------------------------------
预注册（运维在配置里声明）、CIMD（客户端自证一个 HTTPS URL）、DCR（自助注册，
ADR-0002 §3 的兼容回退）最终都收敛到本模块的 ``validate_client_metadata``。
不这样做的话，三条路径会成为三份规则，而"哪条路径的校验最松"就是攻击者会去走的那条
—— 这正是 ADR §3 不允许"为了保险两边都开"的原因。

首版只承认一种客户端形态：public client（``token_endpoint_auth_method = "none"``）
+ 强制 PKCE。MCP 客户端跑在用户自己的机器上，任何"发给它的 secret"都不是 secret；
与其提供一个看着更安全、实为公开的选项，不如只承认这一种，让它是唯一需要被测试与
分析的信任模型。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.access.scopes import Scope
from app.data.models.oauth import REGISTRATION_SOURCES, OAuthClient

#: 首版支持的能力集合。刻意写死而不是做成配置：这三项共同定义了"public client + PKCE"
#: 这一种形态，任何一项放开都等于引入第二种信任模型 —— 那需要它自己的威胁分析与测试，
#: 不该从一个配置项里悄悄长出来。
SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")
SUPPORTED_RESPONSE_TYPES = ("code",)
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = ("none",)

#: 允许使用明文 http 的 redirect_uri 主机（RFC 8252 §7.3 的 loopback 例外）。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ClientMetadataError(ValueError):
    """客户端元数据不合法。``error`` 用 RFC 7591 / RFC 6749 的注册错误码。"""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class ClientMetadata:
    """一个已通过校验的客户端。存储层与授权端点都只认这个类型。"""

    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    scopes: frozenset[str]
    registration_source: str
    metadata_document_url: str | None = None


def is_loopback_host(host: str | None) -> bool:
    return host is not None and host.lower() in _LOOPBACK_HOSTS


def _validate_redirect_uri(uri: str) -> str:
    """逐条检查一个 redirect_uri。

    只接受 https 与 loopback 上的 http。**刻意不支持自定义 scheme**
    （RFC 8252 §7.1 的 ``com.example.app:/callback``）：那类地址在设备上可被任何
    声称拥有同一 scheme 的应用接管，而本服务承载员工数据。真要支持它，应该是经过
    单独威胁分析的显式决定，而不是在校验函数里顺手放行。
    """
    parts = urlsplit(uri)
    if parts.scheme and parts.scheme not in {"http", "https"}:
        # 单独一条分支，只为了报错能指向**真实原因**。落进下面"必须是绝对 URI"那条
        # 会让人以为是自己少写了 host，而实际上我们是不接受这种 scheme。
        raise ClientMetadataError(
            "invalid_redirect_uri",
            f"redirect_uri must use http or https; custom schemes are not supported: {uri!r}",
        )
    if not parts.scheme or not parts.netloc:
        raise ClientMetadataError("invalid_redirect_uri", f"redirect_uri must be an absolute URI: {uri!r}")
    if parts.fragment:
        # RFC 6749 §3.1.2：redirect_uri 不得带 fragment。放行它等于允许把参数藏进
        # 客户端不解析的位置。
        raise ClientMetadataError("invalid_redirect_uri", f"redirect_uri must not contain a fragment: {uri!r}")
    if parts.scheme == "https":
        return uri
    if parts.scheme == "http" and is_loopback_host(parts.hostname):
        return uri
    raise ClientMetadataError(
        "invalid_redirect_uri",
        f"redirect_uri must use https, or http on a loopback address: {uri!r}",
    )


def redirect_uri_matches(registered: Sequence[str], requested: str) -> bool:
    """请求里的 ``redirect_uri`` 是否被允许。

    默认是**逐字节精确匹配**（RFC 6749 §3.1.2.3）。任何前缀或通配匹配都会被用来把
    授权码送到攻击者的地址，而授权码换来的令牌可以直接读员工数据。

    唯一的放宽是 RFC 8252 §7.3 的 loopback：原生客户端在**每次授权时**临时挑一个
    随机端口，注册阶段不可能预知。对 ``http://127.0.0.1:<port>/path`` 这类地址忽略
    端口，只比较 scheme / host / path。这个放宽是安全的 —— loopback 永远指向用户
    自己这台机器，攻击者无法监听它。
    """
    for candidate in registered:
        if candidate == requested:
            return True
        if _loopback_port_differs(candidate, requested):
            return True
    return False


def _loopback_port_differs(registered: str, requested: str) -> bool:
    left = urlsplit(registered)
    right = urlsplit(requested)
    if left.scheme != "http" or right.scheme != "http":
        return False
    if not (is_loopback_host(left.hostname) and is_loopback_host(right.hostname)):
        return False
    return left.path == right.path and left.query == right.query and not right.fragment


def _string_list(document: Mapping[str, Any], key: str, *, default: Sequence[str]) -> tuple[str, ...]:
    raw = document.get(key)
    if raw is None:
        return tuple(default)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ClientMetadataError("invalid_client_metadata", f"{key} must be an array of strings")
    return tuple(raw)


def _coerce_scope(raw: Any) -> frozenset[str]:
    """把 scope 收窄到本服务的词表。

    不认识的取值**拒绝**而不是丢弃：静默丢弃会让客户端以为申请到了，然后在调用工具时
    收到一个与授权阶段完全无关的 scope 不足错误。
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, str):
        raise ClientMetadataError("invalid_client_metadata", "scope must be a space-separated string")
    known = {scope.value for scope in Scope}
    requested = frozenset(part for part in raw.split(" ") if part)
    unknown = sorted(requested - known)
    if unknown:
        raise ClientMetadataError("invalid_client_metadata", f"unsupported scope(s): {', '.join(unknown)}")
    return requested


def validate_client_metadata(
    document: Mapping[str, Any],
    *,
    client_id: str,
    registration_source: str,
    metadata_document_url: str | None = None,
) -> ClientMetadata:
    """把一份客户端元数据文档校验成 ``ClientMetadata``。

    ``registration_source`` 必须是 ``REGISTRATION_SOURCES`` 之一 —— 它决定了这个
    客户端是"谁放进来的"，而审计必须能分辨这件事（尤其是 DCR 自助注册的那些）。
    """
    if registration_source not in REGISTRATION_SOURCES:
        raise ValueError(f"unknown registration_source: {registration_source!r}")

    raw_uris = document.get("redirect_uris")
    if not isinstance(raw_uris, list) or not raw_uris:
        raise ClientMetadataError("invalid_redirect_uri", "redirect_uris is required and must be a non-empty array")
    if not all(isinstance(item, str) for item in raw_uris):
        raise ClientMetadataError("invalid_redirect_uri", "redirect_uris must be an array of strings")
    redirect_uris = tuple(_validate_redirect_uri(str(item)) for item in raw_uris)

    grant_types = _string_list(document, "grant_types", default=("authorization_code",))
    unsupported_grants = sorted(set(grant_types) - set(SUPPORTED_GRANT_TYPES))
    if unsupported_grants:
        raise ClientMetadataError(
            "invalid_client_metadata", f"unsupported grant_type(s): {', '.join(unsupported_grants)}"
        )

    response_types = _string_list(document, "response_types", default=("code",))
    unsupported_responses = sorted(set(response_types) - set(SUPPORTED_RESPONSE_TYPES))
    if unsupported_responses:
        raise ClientMetadataError(
            "invalid_client_metadata", f"unsupported response_type(s): {', '.join(unsupported_responses)}"
        )

    auth_method = document.get("token_endpoint_auth_method")
    if auth_method is not None and auth_method not in SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS:
        raise ClientMetadataError(
            "invalid_client_metadata",
            f"token_endpoint_auth_method must be one of {', '.join(SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS)}; "
            "this authorization server only issues public clients",
        )

    client_name = document.get("client_name")
    if client_name is not None and not isinstance(client_name, str):
        raise ClientMetadataError("invalid_client_metadata", "client_name must be a string")

    return ClientMetadata(
        client_id=client_id,
        client_name=(client_name or "").strip() or client_id,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        response_types=response_types,
        scopes=_coerce_scope(document.get("scope")),
        registration_source=registration_source,
        metadata_document_url=metadata_document_url,
    )


def client_to_row(metadata: ClientMetadata) -> OAuthClient:
    return OAuthClient(
        client_id=metadata.client_id,
        client_name=metadata.client_name,
        redirect_uris=list(metadata.redirect_uris),
        grant_types=list(metadata.grant_types),
        response_types=list(metadata.response_types),
        scope=" ".join(sorted(metadata.scopes)),
        token_endpoint_auth_method="none",
        registration_source=metadata.registration_source,
        metadata_document_url=metadata.metadata_document_url,
    )


def row_to_client(row: OAuthClient) -> ClientMetadata:
    return ClientMetadata(
        client_id=row.client_id,
        client_name=row.client_name,
        redirect_uris=tuple(row.redirect_uris or ()),
        grant_types=tuple(row.grant_types or ()),
        response_types=tuple(row.response_types or ()),
        scopes=frozenset(part for part in (row.scope or "").split(" ") if part),
        registration_source=row.registration_source,
        metadata_document_url=row.metadata_document_url,
    )


async def save_client(session: AsyncSession, metadata: ClientMetadata) -> None:
    """按 ``client_id`` 覆盖写入。

    覆盖而不是"存在即忽略"：CIMD 文档的 ``redirect_uris`` 会在客户端侧演进，预注册
    配置也会被运维修改 —— 两种情况下"忽略更新"都会让数据库停在一个谁都不再维护的旧值上。
    """
    row = await session.get(OAuthClient, metadata.client_id)
    if row is None:
        session.add(client_to_row(metadata))
        return
    row.client_name = metadata.client_name
    row.redirect_uris = list(metadata.redirect_uris)
    row.grant_types = list(metadata.grant_types)
    row.response_types = list(metadata.response_types)
    row.scope = " ".join(sorted(metadata.scopes))
    row.registration_source = metadata.registration_source
    row.metadata_document_url = metadata.metadata_document_url


async def load_client(session: AsyncSession, client_id: str) -> ClientMetadata | None:
    row = await session.get(OAuthClient, client_id)
    return row_to_client(row) if row is not None else None
