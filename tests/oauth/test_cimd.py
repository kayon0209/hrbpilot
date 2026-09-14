"""Client ID Metadata Documents（CIMD）的获取、自证与缓存。

这个文件测的是 AS **唯一**一条"由未认证调用方指定 URL、服务端主动去访问"的路径，因此
两组用例是分开的、也都不能省：

1. **SSRF 守卫**：目标由调用方决定，防护必须独立成立。这部分用纯函数与一个被替换的
   DNS 解析来测 —— 它不该依赖网络，也不该依赖外部域名。
2. **抓取与自证**：这部分**必须真的发出一趟 HTTPS 请求**。``resolve_cimd_client`` 的
   全部意义是"抓回来，确认文档里写的 ``client_id`` 与我抓它用的 URL 逐字节相同"，把
   抓取换成桩等于把这条不变式连同模块的存在理由一起测掉。

所以本地起一个自签证书的 HTTPS 服务器（``scripts/cimd_document_server.py``），并显式
打开两个开关：允许抓取环回地址（仅非生产）、信任那份自签根。这两个开关都不改变任何
校验步骤本身 —— 自证、大小上限、重定向禁用、TLS 校验一个都没少，后面有各自的用例。
"""

from __future__ import annotations

import asyncio
import datetime
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config.settings import settings
from app.data.models.oauth import OAuthClient
from app.oauth import storage as oauth_storage
from app.oauth.cimd import (
    MAX_DOCUMENT_BYTES,
    _assert_host_resolves_to_public_ips,
    _is_public_address,
    _tls_verification_setting,
    resolve_cimd_client,
    validate_metadata_document_url,
)
from app.oauth.clients import ClientMetadataError
from app.oauth.registry import resolve_client
from scripts.cimd_document_server import (
    DocumentServer,
    generate_self_signed_certificate,
    start_document_server,
)

_REDIRECT_URI = "http://127.0.0.1:9/callback"
"""CIMD 文档里声明的回调地址。用环回地址是 RFC 8252 允许的形态，且不需要真的监听。"""


def _document(**overrides: Any) -> dict[str, object]:
    document: dict[str, object] = {
        "client_id": "placeholder",
        "client_name": "本地验收客户端",
        "redirect_uris": [_REDIRECT_URI],
        "scope": "hrb:policy:read",
    }
    document.update(overrides)
    return document


# --------------------------------------------------------------------------- 固定资产


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """自签证书与私钥。

    模块级：RSA 密钥生成是这里最慢的一步，而证书本身与用例无关 —— 每个用例各生成一次
    只会让这个文件跑得慢，不会让它更可信。
    """
    directory = tmp_path_factory.mktemp("cimd-tls")
    cert_path, key_path = directory / "cimd-ca.pem", directory / "cimd-key.pem"
    generate_self_signed_certificate(cert_path, key_path)
    return cert_path, key_path


@pytest.fixture()
def document_server(tls_material: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> Iterator[DocumentServer]:
    """一个本地 HTTPS 文档服务器，并把它显式接进配置。

    两个 ``monkeypatch`` 就是"允许抓取环回地址"与"信任这份自签根"。前者在
    production / staging 下设置会让进程启动失败（见
    ``tests/shared/test_settings_cimd_reach.py``），所以它只可能出现在这里。
    """
    cert_path, key_path = tls_material
    server = start_document_server(cert_path, key_path)
    monkeypatch.setattr(settings, "oauth_cimd_allowed_private_hosts_raw", "127.0.0.1")
    monkeypatch.setattr(settings, "oauth_cimd_ca_bundle", str(cert_path))
    try:
        yield server
    finally:
        server.stop()


# --------------------------------------------------------------------------- URL 形式


def test_a_plain_https_url_with_a_path_is_accepted() -> None:
    url = "https://client.example.test/metadata.json"
    assert validate_metadata_document_url(url) == url


def test_http_is_rejected() -> None:
    """降级到 http 会让"这份文档属于这个 URL"退回成"谁都能在中间改它"。"""
    with pytest.raises(ClientMetadataError):
        validate_metadata_document_url("http://client.example.test/metadata.json")


def test_a_bare_origin_is_rejected() -> None:
    """要求带 path：根 URL 常常会在不经意间被重新指向别处，带 path 的更像刻意声明。"""
    with pytest.raises(ClientMetadataError):
        validate_metadata_document_url("https://client.example.test")
    with pytest.raises(ClientMetadataError):
        validate_metadata_document_url("https://client.example.test/")


def test_a_fragment_is_rejected() -> None:
    """``#`` 之后的部分不会发给服务器，却会进入字符串比较 —— 那就是一条伪造路径。"""
    with pytest.raises(ClientMetadataError):
        validate_metadata_document_url("https://client.example.test/m.json#client.example.test/m.json")


def test_embedded_credentials_are_rejected() -> None:
    with pytest.raises(ClientMetadataError):
        validate_metadata_document_url("https://user:pass@client.example.test/m.json")


# --------------------------------------------------------------------------- SSRF 守卫


@pytest.mark.parametrize(
    "raw",
    [
        "127.0.0.1",  # 环回
        "10.1.2.3",  # 私网
        "192.168.1.1",  # 私网
        "172.16.0.1",  # 私网
        "169.254.169.254",  # 云实例元数据 —— 这条是整段防护最想挡住的那个地址
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # 组播
        "::1",  # IPv6 环回
        "fe80::1",  # IPv6 link-local
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6：不折回 IPv4 判定就能绕过环回检查
        "::ffff:10.0.0.1",
    ],
)
def test_non_public_addresses_are_rejected(raw: str) -> None:
    import ipaddress

    assert _is_public_address(ipaddress.ip_address(raw)) is False, f"{raw} 被判成了公网地址"


def test_a_global_address_is_accepted() -> None:
    """没有这一条，上面那串断言在"函数恒返回 False"时也全绿。"""
    import ipaddress

    assert _is_public_address(ipaddress.ip_address("93.184.216.34")) is True


@pytest.mark.asyncio()
async def test_a_host_resolving_to_both_public_and_private_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合解析结果必须整体拒绝。

    只检查第一个结果时，攻击者可以让 DNS 返回"一个公网 + 一个内网"，然后让 HTTP 客户端
    挑中后者 —— 检查发生在 A 上，连接发生在 B 上。这条用例把"每一个都要是公网"钉住。
    """
    loop = asyncio.get_running_loop()

    async def _mixed(host: str, port: int, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", _mixed)
    with pytest.raises(ClientMetadataError) as excinfo:
        await _assert_host_resolves_to_public_ips("attacker.example.test", 443)
    assert "non-public" in excinfo.value.description


@pytest.mark.asyncio()
async def test_the_allowlist_matches_by_hostname_not_by_resolved_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """白名单按**主机名**匹配。

    若按解析结果放宽，白名单里写一个 ``127.0.0.1`` 就等于放开了所有指向 ``127.0.0.1``
    的名字 —— 而攻击者恰好可以注册这样一个名字。这条用例确认收紧的是主机名。
    """
    monkeypatch.setattr(settings, "oauth_cimd_allowed_private_hosts_raw", "127.0.0.1")
    await _assert_host_resolves_to_public_ips("127.0.0.1", 443)
    with pytest.raises(ClientMetadataError):
        await _assert_host_resolves_to_public_ips("localhost", 443)


def test_tls_verification_can_never_be_disabled() -> None:
    """没有任何配置能让 CIMD 抓取跳过证书校验。

    证书不受信任有正确的解法（把根交给 AS），而"关掉校验"不是它 —— 一旦能关，它就会
    成为某个环境里最先被打开、再也没人关掉的那个开关。
    """
    assert _tls_verification_setting() is True
    settings.oauth_cimd_ca_bundle = "/tmp/some-ca.pem"
    try:
        assert _tls_verification_setting() == "/tmp/some-ca.pem"
    finally:
        settings.oauth_cimd_ca_bundle = ""


# --------------------------------------------------------------------------- 抓取与自证


@pytest.mark.asyncio()
async def test_a_self_certifying_document_is_accepted(document_server: DocumentServer) -> None:
    url = document_server.serve_json("accepted.json", _document())
    # 文档里的 client_id 必须**就是**这个 URL，所以先知道 URL 再回填。
    document_server.serve_json("accepted.json", _document(client_id=url))

    metadata = await resolve_cimd_client(url)
    assert metadata.client_id == url
    assert metadata.registration_source == "cimd"
    assert metadata.metadata_document_url == url
    assert metadata.redirect_uris == (_REDIRECT_URI,)


@pytest.mark.asyncio()
async def test_a_document_claiming_someone_else_is_rejected(document_server: DocumentServer) -> None:
    """CIMD 的**全部意义**在这一条上。

    文档声称自己是另一个 URL（这里是另一个客户端），若接受，任何人只要在自己的域名下
    托管一份 JSON，就能拿到用户对那个名字的授权 —— 那是一个比不实现 CIMD 更糟的状态。
    """
    url = document_server.serve_json("impostor.json", _document(client_id="https://someone-else.test/m.json"))
    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client(url)
    assert "byte-for-byte" in excinfo.value.description


@pytest.mark.asyncio()
async def test_a_near_miss_is_still_rejected(document_server: DocumentServer) -> None:
    """逐字节比较，不是"看起来一样"。尾斜杠、大小写、查询串都算不同。"""
    url = document_server.serve_json("near-miss.json", _document(client_id="placeholder"))
    near_misses = [f"{url}/", url.upper(), f"{url}?v=2"]
    for candidate in near_misses:
        document_server.serve_json("near-miss.json", _document(client_id=candidate))
        with pytest.raises(ClientMetadataError):
            await resolve_cimd_client(url)


@pytest.mark.asyncio()
async def test_a_redirect_is_not_followed(document_server: DocumentServer) -> None:
    """一次 302 就能把已校验的公开地址换成内网地址，让前面所有检查形同虚设。

    这条同时确认了"白名单放宽"没有顺带放宽重定向：被红向的目标本身也在白名单里，
    仍然要被拒。
    """
    target = document_server.serve_json("target.json", _document(client_id="placeholder"))
    source = document_server.serve_raw("redirect.json", status=302, body=b"", content_type="text/plain")
    document_server.routes["/redirect.json"] = (302, "text/plain", b"")
    document_server.routes["/target.json"] = (200, "application/json", b'{"client_id": "x"}')

    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client(source)
    assert "302" in excinfo.value.description
    assert target.endswith("target.json")  # 目标确实存在，拒绝不是因为 404


@pytest.mark.asyncio()
async def test_a_non_200_response_is_rejected(document_server: DocumentServer) -> None:
    url = document_server.serve_raw("broken.json", status=500, body=b"boom", content_type="text/plain")
    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client(url)
    assert "500" in excinfo.value.description


@pytest.mark.asyncio()
async def test_invalid_json_is_rejected(document_server: DocumentServer) -> None:
    url = document_server.serve_raw("not-json.json", status=200, body=b"<html>nope</html>")
    with pytest.raises(ClientMetadataError):
        await resolve_cimd_client(url)


@pytest.mark.asyncio()
async def test_a_json_array_is_rejected(document_server: DocumentServer) -> None:
    """顶层必须是对象：数组合法时，``document.get("client_id")`` 会以 AttributeError 炸掉。"""
    url = document_server.serve_raw("array.json", status=200, body=b"[1, 2, 3]")
    with pytest.raises(ClientMetadataError):
        await resolve_cimd_client(url)


@pytest.mark.asyncio()
async def test_an_oversized_document_is_rejected(document_server: DocumentServer) -> None:
    """上限的意义是让"取回一个 GB"变成一次立即失败的请求，而不是把 AS 的内存吃满。"""
    huge = b"x" * (MAX_DOCUMENT_BYTES + 1)
    url = document_server.serve_raw("huge.json", status=200, body=huge)
    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client(url)
    assert str(MAX_DOCUMENT_BYTES) in excinfo.value.description


@pytest.mark.asyncio()
async def test_an_unreachable_document_server_is_reported_as_such(
    tls_material: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """服务器不可达要与"文档内容不合法"可分辨 —— 两者的处置完全不同。"""
    cert_path, _ = tls_material
    monkeypatch.setattr(settings, "oauth_cimd_allowed_private_hosts_raw", "127.0.0.1")
    monkeypatch.setattr(settings, "oauth_cimd_ca_bundle", str(cert_path))
    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client("https://127.0.0.1:9/unreachable.json")
    assert "could not fetch" in excinfo.value.description


@pytest.mark.asyncio()
async def test_an_untrusted_certificate_is_rejected(
    document_server: DocumentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自签证书在没有把根交给 AS 时必须失败。

    这是"TLS 校验真的在跑"的反向证据：接上 CA 就通（上面那些用例），不接就断。少了
    这一条，``verify=`` 被谁改成 ``False`` 也没有用例会红。
    """
    url = document_server.serve_json("tls.json", _document())
    document_server.serve_json("tls.json", _document(client_id=url))
    monkeypatch.setattr(settings, "oauth_cimd_ca_bundle", "")
    with pytest.raises(ClientMetadataError) as excinfo:
        await resolve_cimd_client(url)
    assert "could not fetch" in excinfo.value.description


# --------------------------------------------------------------------------- 缓存与回退


@pytest.fixture()
async def registry(sqlite_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """把 ``resolve_client`` 的存储换成内存 sqlite。

    用真库而不是假会话：``resolve_client`` 的判断里有一半是"库里那条记录算不算过期"，
    而那是一次真实的读写。
    """
    async with sqlite_engine.begin() as connection:
        await connection.run_sync(OAuthClient.__table__.create)
    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    monkeypatch.setattr(oauth_storage, "get_session_factory", lambda: factory)
    yield factory


async def _age_client_row(factory: Any, client_id: str, *, days: int) -> None:
    """把库里那条记录的 ``updated_at`` 推到过去，模拟缓存过期。"""
    async with factory() as session:
        row = await session.get(OAuthClient, client_id)
        assert row is not None
        row.updated_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        await session.commit()


@pytest.mark.asyncio()
async def test_first_sight_fetches_and_stores(document_server: DocumentServer, registry: Any) -> None:
    url = document_server.serve_json("first.json", _document())
    document_server.serve_json("first.json", _document(client_id=url))

    metadata = await resolve_client(url)
    assert metadata is not None and metadata.registration_source == "cimd"

    async with registry() as session:
        row = await session.get(OAuthClient, url)
    assert row is not None, "首次见到 CIMD 客户端后没有入库 —— 下次授权仍要重新抓取"
    assert row.registration_source == "cimd"
    assert row.metadata_document_url == url


@pytest.mark.asyncio()
async def test_a_fresh_cache_does_not_hit_the_network(document_server: DocumentServer, registry: Any) -> None:
    """保鲜期内不抓取。

    用一个**已经停掉的**文档服务器来证明这一点：能解析出来，就说明这一趟请求没发生。
    换成"数一数被调了几次"需要打桩，而打桩会把抓取路径本身也替换掉。
    """
    url = document_server.serve_json("cached.json", _document())
    document_server.serve_json("cached.json", _document(client_id=url))
    assert await resolve_client(url) is not None

    document_server.stop()
    metadata = await resolve_client(url)
    assert metadata is not None and metadata.client_id == url


@pytest.mark.asyncio()
async def test_a_stale_cache_is_refreshed(document_server: DocumentServer, registry: Any) -> None:
    """过期后重新抓取，并把新文档写回库。

    不重取会让库里停在一个客户端早已不再维护的旧值上（比如旧的 redirect_uris），而
    那个旧值恰好是授权码的去向。
    """
    url = document_server.serve_json("refresh.json", _document())
    document_server.serve_json("refresh.json", _document(client_id=url))
    assert await resolve_client(url) is not None
    await _age_client_row(registry, url, days=2)

    document_server.serve_json("refresh.json", _document(client_id=url, client_name="改过名字的客户端"))
    metadata = await resolve_client(url)
    assert metadata is not None and metadata.client_name == "改过名字的客户端"

    async with registry() as session:
        row = await session.get(OAuthClient, url)
    assert row is not None and row.client_name == "改过名字的客户端"


@pytest.mark.asyncio()
async def test_a_stale_cache_survives_an_outage(document_server: DocumentServer, registry: Any) -> None:
    """客户端自己的文档服务器短暂不可用时，回退到库里已有的那份。

    否则客户端侧的一次重启就会让所有用户**同时**失去重新授权的能力 —— 而他们本来
    已经授权过。数据库里没有那份时才判为未知客户端（下一条用例）。
    """
    url = document_server.serve_json("outage.json", _document())
    document_server.serve_json("outage.json", _document(client_id=url))
    assert await resolve_client(url) is not None
    await _age_client_row(registry, url, days=2)

    document_server.stop()
    metadata = await resolve_client(url)
    assert metadata is not None, "文档服务器不可用时丢弃了库里已有的那份 —— 那会把一次短暂故障放大成全网掉线"


@pytest.mark.asyncio()
async def test_an_unknown_client_without_a_cache_is_unknown(document_server: DocumentServer, registry: Any) -> None:
    """库里没有、抓取又失败 → 未知客户端。

    与上一条配对：回退的是**缓存**，不是"抓取失败也算通过"。少这一条的话，一条
    "抓不到就当已注册"的实现也能让上一条通过。
    """
    document_server.stop()
    assert await resolve_client(f"{document_server.base_url}/never-seen.json") is None


@pytest.mark.asyncio()
async def test_a_non_url_client_id_is_not_fetched(registry: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """不是 https URL 的 ``client_id`` 不会被拿去抓取。

    否则一个预注册客户端的名字（比如 ``workbuddy``）会变成一次对
    ``https://workbuddy`` 的 DNS 查询尝试 —— 一次纯粹的浪费，而且是可被外部观察的。
    """
    calls: list[str] = []

    async def _record(url: str) -> Any:
        calls.append(url)
        raise AssertionError("不该走到抓取")

    monkeypatch.setattr("app.oauth.registry.resolve_cimd_client", _record)
    assert await resolve_client("workbuddy") is None
    assert calls == []
