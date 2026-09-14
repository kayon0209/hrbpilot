"""Client ID Metadata Documents（CIMD）的获取与校验。

CIMD 是现行 MCP 授权规范推荐的客户端注册方式（DCR 已被弃用）。客户端把自己的
``client_id`` 设成**一个 HTTPS URL**，授权服务器去取那份文档。这条路成立的前提是
**文档确实属于它所在的那个 URL** —— 否则任何人都能在自己域名下托管一份声称自己是
别人的文档，然后拿到用户对那个名字的授权。

两个独立的风险面
----------------
1. **自证性**：``document["client_id"]`` 必须与抓取用的 URL 逐字节相同（见
   ``resolve_cimd_client``）。少这一条，CIMD 就退化成"任何人可以自称任何人"。
2. **SSRF**：URL 完全由未认证的调用方提供，而 AS 会主动去请求它。没有防护的话，
   这个端点就是一个"让服务器替我访问内网"的工具 —— 云环境的实例元数据地址
   （``169.254.169.254``）、``127.0.0.1`` 上的管理端口、Kubernetes 的 service 网段
   都能被探测。防护在 ``_assert_host_resolves_to_public_ips`` 里做，并且**禁用重定向**：
   一次 ``302`` 就能把"公开地址"换成"内网地址"，那等于前面所有检查都没有发生。

可验证性
--------
上面第一条不变式（文档自证）只有在**真的发出一趟 HTTPS 请求**时才会被执行，所以
本地验收需要一个允许命中环回地址的出口（``OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS``，生产
不可用）与一份可信任的自签根（``OAUTH_CIMD_CA_BUNDLE``）。两者都不改变校验逻辑本身：
放宽的只是"这个主机名允不允许解析到内网"，自证、大小上限、重定向禁用、TLS 校验
一个都不少。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config.settings import settings
from app.oauth.clients import ClientMetadata, ClientMetadataError, validate_client_metadata
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 文档大小上限。CIMD 文档是一小段 JSON；上限的意义是让"取回一个 GB"变成一次立即
#: 失败的请求，而不是把 AS 的内存吃满。
MAX_DOCUMENT_BYTES = 64 * 1024
_FETCH_TIMEOUT_SECONDS = 5.0

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def validate_metadata_document_url(url: str) -> str:
    """校验 ``client_id`` 作为 CIMD 文档 URL 的形式要求。

    必须 https、必须带 path、不得带 fragment 或 userinfo。要求带 path 是为了排除
    "把根 URL 当身份"的用法 —— 根 URL 常常会在不经意间被重新指向别处，而带 path 的
    URL 更像一个刻意声明。
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ClientMetadataError("invalid_client", f"client_id must be an https URL: {url!r}")
    if not parts.netloc or not parts.hostname:
        raise ClientMetadataError("invalid_client", f"client_id must be an absolute URL: {url!r}")
    if parts.username or parts.password:
        raise ClientMetadataError("invalid_client", "client_id must not embed credentials")
    if parts.fragment:
        raise ClientMetadataError("invalid_client", "client_id must not contain a fragment")
    if not parts.path.strip("/"):
        raise ClientMetadataError("invalid_client", "client_id must contain a path component")
    return url


def _is_public_address(address: _IPAddress) -> bool:
    """这个 IP 是否可以在"代替调用方去访问"的场景里使用。"""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 这类映射地址必须按 IPv4 判定，否则环回检查会被绕过。
        address = address.ipv4_mapped
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    return address.is_global


async def _assert_host_resolves_to_public_ips(host: str, port: int) -> None:
    """解析主机名，并要求**每一个**解析结果都是公网地址。

    要求"全部"而不是"至少一个"：只检查第一个结果时，攻击者可以让 DNS 返回一个公网
    地址加一个内网地址，然后让 HTTP 客户端挑中后者。

    白名单（``OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS``）**在生产/预发下不可用** —— 配置层
    会让进程启动失败，所以这里的放宽只可能出现在开发环境。每次命中都记一条 warning：
    一个本该只出现于本地验收的放宽，若在别处被打开，日志里要看得见。
    """
    if host.strip().lower() in settings.oauth_cimd_allowed_private_hosts:
        logger.warning("oauth_cimd_private_host_allowed", host=host, note="SSRF 守卫被显式放宽（仅限非生产）")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ClientMetadataError("invalid_client", f"client_id host could not be resolved: {host}") from exc
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise ClientMetadataError("invalid_client", f"client_id host resolved to no addresses: {host}")
    for raw in addresses:
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ClientMetadataError(
                "invalid_client", f"client_id host resolved to an invalid address: {host}"
            ) from exc
        if not _is_public_address(parsed):
            raise ClientMetadataError("invalid_client", f"client_id host resolves to a non-public address: {host}")
    # 残留风险（已知并记录）：检查与实际连接是两次独立的解析，理论上存在 DNS rebinding
    # 窗口。彻底消除需要把连接固定到已校验的 IP，httpx 不直接支持；这里用"禁用重定向 +
    # 校验全部解析结果"把窗口压到最小，并在威胁模型 T-6 里登记。


def _tls_verification_setting() -> bool | str:
    """CIMD 抓取时的 TLS 校验参数。

    ``OAUTH_CIMD_CA_BUNDLE`` 指向一份 PEM 根证书（企业私有 PKI，或本地验收用的自签
    根）。留空时返回 ``True``（走默认信任库）。**不会**返回 ``False`` —— 没有任何配置
    能关掉 TLS 校验：证书不受信任这件事有正确的解法，而关掉校验不是它。
    """
    bundle = settings.oauth_cimd_ca_bundle.strip()
    return bundle or True


async def fetch_metadata_document(url: str) -> dict[str, Any]:
    """取回一份 CIMD 文档（未做自证性校验）。"""
    validate_metadata_document_url(url)
    parts = urlsplit(url)
    host = parts.hostname or ""
    await _assert_host_resolves_to_public_ips(host, parts.port or 443)

    # ``_tls_verification_setting`` 在配置了 CA bundle 时返回字符串路径；httpx 的
    # ``verify=<str>`` 已被弃用并会刷 DeprecationWarning，这里转成等价且受支持的
    # ``SSLContext``（加载默认信任库并额外信任该 bundle）。
    setting = _tls_verification_setting()
    verify: bool | ssl.SSLContext
    verify = ssl.create_default_context(cafile=setting) if isinstance(setting, str) else setting

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS,
            # 禁用重定向：一次 302 就能把已校验的公开地址换成内网地址，
            # 让前面所有检查形同虚设。
            follow_redirects=False,
            verify=verify,
            headers={"Accept": "application/json"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ClientMetadataError("invalid_client", f"could not fetch client metadata document: {url}") from exc

    if response.status_code != 200:
        raise ClientMetadataError(
            "invalid_client", f"client metadata document returned HTTP {response.status_code}: {url}"
        )
    if len(response.content) > MAX_DOCUMENT_BYTES:
        raise ClientMetadataError(
            "invalid_client", f"client metadata document exceeds {MAX_DOCUMENT_BYTES} bytes: {url}"
        )
    try:
        document = response.json()
    except json.JSONDecodeError as exc:
        raise ClientMetadataError("invalid_client", f"client metadata document is not valid JSON: {url}") from exc
    if not isinstance(document, dict):
        raise ClientMetadataError("invalid_client", f"client metadata document must be a JSON object: {url}")
    return document


async def resolve_cimd_client(url: str) -> ClientMetadata:
    """取回并校验一份 CIMD 文档，返回可直接入库的客户端元数据。"""
    document = await fetch_metadata_document(url)
    declared = document.get("client_id")
    if declared != url:
        # CIMD 的**全部意义**在这一行：文档必须自证它属于抓取用的那个 URL。
        # 允许两者不同，等于允许任何人在自己的域名下托管一份声称是别人的文档。
        raise ClientMetadataError(
            "invalid_client",
            "client metadata document must declare a 'client_id' byte-for-byte identical to the URL it was "
            "fetched from",
        )
    # client_id 一律以**抓取用的 URL** 为准，而不是文档里那个（两者此时已相等）。
    return validate_client_metadata(document, client_id=url, registration_source="cimd", metadata_document_url=url)
