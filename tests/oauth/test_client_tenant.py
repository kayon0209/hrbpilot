"""客户端租户归属：谁能声明租户、谁能改租户，两条都必须是"没人能意外绕过"。

租户决定 AS 登录去哪个用户目录（``users`` 受 RLS）找人，因此它是一道信任边界：

- 注册**文档**（DCR / CIMD）里写了 ``tenant_id`` 也必须被无视 —— 文档由匿名请求
  提供，让它自选租户等于把跨租户入口写在协议里；
- 只有**预注册配置**（运维声明的信任关系）能带租户，且写错要让启动失败；
- 建好之后，任何一次文档刷新都不得把客户端**悄悄搬去**别的租户。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config.settings import settings
from app.data.models.oauth import DEFAULT_TENANT, OAuthClient
from app.oauth.clients import client_to_row, row_to_client, save_client, validate_client_metadata
from app.oauth.registry import preconfigured_clients

_DOCUMENT: dict[str, Any] = {
    "client_id": "dcr_tenant-test",
    "redirect_uris": ["http://127.0.0.1:9/callback"],
    "scope": "hrb:profile:read",
}


def test_a_registration_document_cannot_claim_a_tenant() -> None:
    """DCR 文档自带 ``tenant_id`` 也必须被无视 —— 否则匿名请求自选租户。"""
    document = {**_DOCUMENT, "tenant_id": "acme"}
    metadata = validate_client_metadata(document, client_id="dcr_x", registration_source="dcr")
    assert metadata.tenant_id == DEFAULT_TENANT


def test_a_cimd_document_cannot_claim_a_tenant() -> None:
    document = {**_DOCUMENT, "client_id": "https://client.example.com/meta.json", "tenant_id": "acme"}
    metadata = validate_client_metadata(
        document,
        client_id="https://client.example.com/meta.json",
        registration_source="cimd",
        metadata_document_url="https://client.example.com/meta.json",
    )
    assert metadata.tenant_id == DEFAULT_TENANT


def test_pre_registered_config_can_declare_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "oauth_pre_registered_clients",
        json.dumps([{**_DOCUMENT, "client_id": "prereg-acme", "tenant_id": "acme"}]),
    )
    clients = preconfigured_clients()
    assert [client.tenant_id for client in clients] == ["acme"]


def test_pre_registered_config_without_a_tenant_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "oauth_pre_registered_clients", json.dumps([{**_DOCUMENT}]))
    clients = preconfigured_clients()
    assert [client.tenant_id for client in clients] == [DEFAULT_TENANT]


@pytest.mark.parametrize("bad", ["", "   ", 123, None])
def test_an_invalid_tenant_in_pre_registered_config_fails_startup(monkeypatch: pytest.MonkeyPatch, bad: Any) -> None:
    """预注册是运维的显式决定，写错租户要让启动失败，而不是静默落回缺省租户。

    ``None`` 在这里是 JSON ``null``（键存在但值为空）—— 与"没写这个键"是两回事。
    """
    entry: dict[str, Any] = {**_DOCUMENT, "client_id": "prereg-bad", "tenant_id": bad}
    monkeypatch.setattr(settings, "oauth_pre_registered_clients", json.dumps([entry]))
    with pytest.raises(ValueError):
        preconfigured_clients()


def test_the_tenant_survives_the_row_roundtrip() -> None:
    metadata = validate_client_metadata(
        _DOCUMENT, client_id="prereg-x", registration_source="pre_registered", tenant_id="acme"
    )
    assert row_to_client(client_to_row(metadata)).tenant_id == "acme"


@pytest.fixture()
async def client_factory(sqlite_engine: AsyncEngine) -> async_sessionmaker[Any]:
    async with sqlite_engine.begin() as connection:
        await connection.run_sync(OAuthClient.__table__.create)
    return async_sessionmaker(sqlite_engine, expire_on_commit=False)


async def test_refreshing_a_client_never_moves_it_to_another_tenant(
    client_factory: async_sessionmaker[Any],
) -> None:
    """建库时是 acme，一次"文档刷新"（tenant 为缺省值）之后必须还是 acme。"""
    original = validate_client_metadata(
        _DOCUMENT, client_id="prereg-acme", registration_source="pre_registered", tenant_id="acme"
    )
    async with client_factory() as session:
        await save_client(session, original)
        await session.commit()

    refreshed = validate_client_metadata(
        {**_DOCUMENT, "client_name": "改名了"}, client_id="prereg-acme", registration_source="cimd"
    )
    assert refreshed.tenant_id == DEFAULT_TENANT
    async with client_factory() as session:
        await save_client(session, refreshed)
        await session.commit()

    async with client_factory() as session:
        row = await session.get(OAuthClient, "prereg-acme")
    assert row is not None
    assert row.tenant_id == "acme"
    assert row.client_name == "改名了"
