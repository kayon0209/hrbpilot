"""把 ``client_id`` 解析成一个已注册的客户端。

三条注册路径在**运行时**收敛到这一处
------------------------------------
预注册（启动时从配置同步进库）、CIMD（首次见到时抓取文档并入库）、DCR（注册端点
直接写入）最终都落在 ``oauth_clients`` 表上，而授权端点只调用 ``resolve_client``。
这样"某个客户端能不能用"只有一条判定路径可查，不会出现"预注册的走了 A 逻辑、
CIMD 的走了 B 逻辑"这种需要同时读两处才能回答的问题。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from app.config.settings import settings
from app.data.models.oauth import OAuthClient
from app.oauth.cimd import resolve_cimd_client
from app.oauth.clients import (
    ClientMetadata,
    ClientMetadataError,
    row_to_client,
    save_client,
    validate_client_metadata,
)
from app.oauth.storage import new_opaque_value, oauth_session
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: DCR 生成的 client_id 前缀。用固定人类可读前缀而不是纯随机串：审计里看到
#: ``dcr_...`` 就知道"这是自助注册进来的"，与 CIMD 的 URL 形式、预注册的具名形式
#: 一眼可分。
DCR_CLIENT_ID_PREFIX = "dcr_"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(moment: datetime.datetime | None) -> datetime.datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC)


def _looks_like_metadata_document_url(client_id: str) -> bool:
    return client_id.startswith("https://")


def _is_stale(row: OAuthClient, now: datetime.datetime) -> bool:
    """CIMD 客户端的缓存是否过期。非 CIMD 的永不过期（它们的真相源在别处）。"""
    if row.registration_source != "cimd":
        return False
    updated = _as_utc(row.updated_at)
    if updated is None:
        return True
    return (now - updated).total_seconds() > settings.oauth_cimd_cache_ttl_seconds


async def resolve_client(client_id: str) -> ClientMetadata | None:
    """解析客户端；未知则返回 ``None``（调用方按 ``invalid_client`` 处理）。

    CIMD 抓取失败时**回退到库里已有的那份**：客户端自己的文档服务器短暂不可用，
    不应该让已经完成过授权的用户突然无法重新授权。若库里也没有，才判定为未知客户端。
    """
    async with oauth_session() as session:
        row = await session.get(OAuthClient, client_id)

    if row is not None and not _is_stale(row, _now()):
        return row_to_client(row)

    if not _looks_like_metadata_document_url(client_id):
        return None

    try:
        metadata = await resolve_cimd_client(client_id)
    except ClientMetadataError as exc:
        if row is not None:
            logger.warning("oauth_cimd_refresh_failed_using_cached", client_id=client_id, error=exc.description)
            return row_to_client(row)
        logger.warning("oauth_cimd_resolution_failed", client_id=client_id, error=exc.description)
        return None

    async with oauth_session() as session:
        await save_client(session, metadata)
    return metadata


def _preconfigured_documents() -> list[dict[str, Any]]:
    raw = settings.oauth_pre_registered_clients.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OAUTH_PRE_REGISTERED_CLIENTS must be a JSON array") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("OAUTH_PRE_REGISTERED_CLIENTS must be a JSON array of objects")
    return parsed


def preconfigured_clients() -> tuple[ClientMetadata, ...]:
    """配置里声明的预注册客户端，已通过与他人相同的校验。

    **不**在这里容忍半个合法项：任何一项不合法就让整个启动失败。预注册客户端是运维
    刻意声明的信任关系，一条写错的 redirect_uri 意味着授权码会被送到一个没人打算
    信任的地址 —— 静默跳过它只会让这个事实在事故之后才被发现。
    """
    clients: list[ClientMetadata] = []
    for document in _preconfigured_documents():
        client_id = document.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise ValueError("OAUTH_PRE_REGISTERED_CLIENTS entries must carry a non-empty string 'client_id'")
        clients.append(validate_client_metadata(document, client_id=client_id, registration_source="pre_registered"))
    return tuple(clients)


async def sync_preconfigured_clients() -> int:
    """把配置里的预注册客户端同步进数据库。返回同步的数量。

    AS 启动时调用。配置是声明，库是查询入口 —— 两者必须一致，否则"为什么这个
    客户端能用"的答案会分裂成两处。
    """
    clients = preconfigured_clients()
    async with oauth_session() as session:
        for metadata in clients:
            await save_client(session, metadata)
    if clients:
        logger.info("oauth_preconfigured_clients_synced", count=len(clients), clients=[c.client_id for c in clients])
    return len(clients)


async def register_dynamic_client(document: dict[str, Any], client_id: str) -> ClientMetadata:
    """写入一个 DCR 客户端。``client_id`` 由调用方生成（见 ``new_dynamic_client_id``）。"""
    metadata = validate_client_metadata(document, client_id=client_id, registration_source="dcr")
    async with oauth_session() as session:
        await save_client(session, metadata)
    logger.info("oauth_dynamic_client_registered", client_id=client_id)
    return metadata


def new_dynamic_client_id() -> str:
    """生成一个 DCR 客户端的 ``client_id``。"""
    return f"{DCR_CLIENT_ID_PREFIX}{new_opaque_value()}"
