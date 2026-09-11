"""Fetch clients for the first batch: WeCom messages/files, Feishu docs.

All fetches:
- decrypt the tenant credential at use time (never cache plaintext),
- pass through the per-platform rate limiter,
- record sync failures on the data source row (visible to admins),
- return platform-agnostic items the ingestion pipeline can consume.
"""

from __future__ import annotations

import httpx

from app.connectors.credentials import decrypt_credential
from app.connectors.registry import FEISHU, WECOM
from app.connectors.sync import limiter_for, mark_sync_failed, mark_sync_ok
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

TIMEOUT_SECONDS = 30.0


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


def _check_wecom_error(payload: dict, context: str) -> None:
    errcode = payload.get("errcode")
    if errcode not in (0, None):
        raise AppError(
            f"企业微信 {context}失败：{payload.get('errmsg')}",
            code="CONNECTOR_ERROR",
            status_code=502,
        )


async def fetch_wecom_messages(
    tenant_id: str,
    source_id: str,
    app_secret_encrypted: bytes,
    corpid: str,
    cursor: str | None = None,
    limit: int = 100,
) -> tuple[list[dict], str, bool]:
    """Pull one bounded batch of chat updates via the app access token.

    Scope: only chats the app was explicitly granted (企业微信通讯录/会话
    接口), never a tenant-wide crawl — authorized_scope governs the chat ids
    the admin configured on the data source.  Returns ``(items, next_cursor,
    has_more)`` so the runner can resume exactly and page until drained.

    This function does NOT write sync_status — the runner owns success/failure
    so a partial page never looks like a completed sync.
    """
    # CONN-05: rate limit keyed by (tenant, source) so one noisy source cannot
    # consume the whole platform's budget (or trip a provider-side ban).
    limiter_for(f"wecom:{tenant_id}:{source_id}").check()
    secret = decrypt_credential(tenant_id, app_secret_encrypted)
    try:
        async with _client() as client:
            token_response = await client.get(
                f"{WECOM.api_base}/gettoken", params={"corpid": corpid, "corpsecret": secret}
            )
            token_payload = token_response.json()
            _check_wecom_error(token_payload, "获取应用令牌")
            access_token = token_payload["access_token"]

            body: dict = {"limit": limit}
            if cursor:
                body["seq"] = int(cursor)
            response = await client.post(
                f"{WECOM.api_base}/appchat/getchatdata",
                params={"access_token": access_token},
                json=body,
            )
            payload = response.json()
            _check_wecom_error(payload, "拉取消息")
        items = [
            {
                "external_id": str(item.get("msgid", "")),
                "chat": item.get("chatid"),
                "sender": item.get("from", {}).get("userid"),
                "content": item.get("text", {}).get("content", ""),
                "occurred_at": item.get("msgtime"),
            }
            for item in payload.get("chatdata", [])
        ]
        next_cursor = str(payload.get("seq", cursor or "0"))
        has_more = bool(payload.get("has_more", False))
        return items, next_cursor, has_more
    except AppError as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise
    except Exception as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise


async def fetch_wecom_media(
    tenant_id: str,
    source_id: str,
    app_secret_encrypted: bytes,
    corpid: str,
    media_id: str,
) -> bytes:
    """Download one authorized media file (bounded to what admin configured)."""
    limiter_for(f"wecom:{tenant_id}:{source_id}").check()
    secret = decrypt_credential(tenant_id, app_secret_encrypted)
    try:
        async with _client() as client:
            token_response = await client.get(
                f"{WECOM.api_base}/gettoken", params={"corpid": corpid, "corpsecret": secret}
            )
            token_payload = token_response.json()
            _check_wecom_error(token_payload, "获取应用令牌")
            response = await client.get(
                f"{WECOM.api_base}/media/get",
                params={"access_token": token_payload["access_token"], "media_id": media_id},
            )
            response.raise_for_status()
        await mark_sync_ok(tenant_id, source_id)
        return response.content
    except AppError as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise
    except Exception as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise


async def fetch_feishu_document(
    tenant_id: str,
    source_id: str,
    user_access_token_encrypted: bytes,
    document_id: str,
) -> dict:
    """Read one document's content (docx blocks) the user authorized.

    Reads are per-document and explicit — no drive-wide crawling.
    """
    limiter_for(f"feishu:{tenant_id}:{source_id}").check()
    token = decrypt_credential(tenant_id, user_access_token_encrypted)
    try:
        async with _client() as client:
            response = await client.get(
                f"{FEISHU.api_base}/docx/v1/documents/{document_id}/raw_content",
                headers={"Authorization": f"Bearer {token}"},
            )
            payload = response.json()
            code = payload.get("code", 0)
            if code != 0:
                raise AppError(
                    f"飞书文档读取失败：{payload.get('msg')}",
                    code="CONNECTOR_ERROR",
                    status_code=502,
                )
        await mark_sync_ok(tenant_id, source_id)
        return {
            "external_id": document_id,
            "content": payload.get("data", {}).get("content", ""),
            "title": payload.get("data", {}).get("title"),
        }
    except AppError as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise
    except Exception as exc:
        await mark_sync_failed(tenant_id, source_id, str(exc))
        raise
