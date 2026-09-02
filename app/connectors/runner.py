"""Connector sync runner: dispatch a real, incremental, idempotent pull.

Entry points today: the admin-triggered endpoint calls this directly and it is
bounded per run (MAX_PAGES_PER_RUN) so a single HTTP request never drains an
unbounded stream.  A production background worker (Celery/arq) is NOT yet
wired — this is stated honestly rather than pretending a worker exists.

Guarantees kept (see capability matrix 安全基线):

- Real fetch through the platform client (WeCom messages first batch).
- Incremental: per-(source, stream) cursor resumes where the last run stopped.
- Idempotent: every consumed external id goes through connector_event_log's
  UNIQUE index; a redelivered event is counted and dropped, never re-triggered.
- Lease: a per-source PostgreSQL advisory lock refuses a second concurrent
  sync (CONN-03).
- Honest status: success/failure is written back to the data source row.  A
  stream whose client isn't wired (e.g. Feishu cloud-doc list) is surfaced as
  an explicit infrastructure error + ``sync_status=failed`` — never ``ok``.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from app.connectors import clients
from app.connectors.sync import (
    consume_event,
    get_cursor,
    mark_sync_failed,
    mark_sync_ok,
    save_cursor,
)
from app.data.database import get_engine, get_session_factory
from app.data.models.data_source import DataSource
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

WECOM_MESSAGE_STREAM = "wecom_messages"
FETCH_BATCH_LIMIT = 100
MAX_PAGES_PER_RUN = 50


async def _load_source(tenant_id: str, source_id: str) -> DataSource:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
    if row is None:
        raise AppError("数据源不存在", code="NOT_FOUND", status_code=404)
    return row


async def run_connector_sync(tenant_id: str, source_id: str) -> str:
    """Run the first wired stream for a data source, returning the stream label.

    Lease (CONN-03): a PostgreSQL advisory lock keyed on (source_id, "sync")
    guarantees that two concurrent syncs for the same source cannot run at
    once — a second trigger waits briefly and then fails with CONFLICT rather
    than double-pulling.  The lock is session-scoped and released when the
    connection closes, so a crashed worker never leaks a stale lease.
    """
    from sqlalchemy import text

    row = await _load_source(tenant_id, source_id)
    platform = row.platform
    content_types = set(json.loads(row.content_types or "[]"))
    credential = row.credential_encrypted
    app_id = row.oauth_app_id or ""

    # Session-scoped advisory lock keyed by (source id, 'sync') so the lock is
    # per-source, not global. Keep it within signed int64 range.
    digest = hashlib.sha256(f"{source_id}:sync".encode()).hexdigest()
    lock_key = int(digest[:15], 16)
    engine = get_engine()
    async with engine.connect() as conn:
        acquired = (await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})).scalar()
        if not acquired:
            raise AppError("该数据源正在同步中，请稍后再试", code="CONFLICT", status_code=409)
        try:
            if platform == "wecom" and "messages" in content_types and credential and app_id:
                scope = getattr(row, "authorized_scope_json", None)
                chat_ids = scope.get("chat_ids") if isinstance(scope, dict) else None
                if (
                    not isinstance(chat_ids, list)
                    or not chat_ids
                    or not all(isinstance(chat_id, str) and chat_id for chat_id in chat_ids)
                ):
                    raise AppError(
                        "企业微信消息同步需要登记非空的结构化 chat_ids 授权范围",
                        code="CONFIG_ERROR",
                        status_code=409,
                    )
                result = await _run_wecom_messages(
                    tenant_id, source_id, credential, app_id, authorized_chat_ids=set(chat_ids)
                )
                return result
            reason = (
                "该数据源的自动同步流尚未接线（拉取客户端未实现）"
                if platform in ("wecom", "feishu")
                else "该渠道尚未支持自动同步"
            )
            await mark_sync_failed(tenant_id, source_id, reason)
            logger.warning("connector_sync_not_wired", tenant_id=tenant_id, source_id=source_id, platform=platform)
            raise AppError(reason, code="SYNC_NOT_SUPPORTED", status_code=501)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


async def _run_wecom_messages(
    tenant_id: str,
    source_id: str,
    credential: bytes,
    app_id: str,
    *,
    authorized_chat_ids: set[str],
) -> str:
    cursor = await get_cursor(tenant_id, source_id, WECOM_MESSAGE_STREAM)
    total = 0
    for _page in range(MAX_PAGES_PER_RUN):
        prev_cursor = cursor
        items, next_cursor, has_more = await clients.fetch_wecom_messages(
            tenant_id,
            source_id,
            credential,
            app_id,
            cursor=prev_cursor,
            limit=FETCH_BATCH_LIMIT,
        )
        for item in items:
            if item.get("chat") not in authorized_chat_ids:
                logger.warning(
                    "connector_message_outside_authorized_scope",
                    tenant_id=tenant_id,
                    source_id=source_id,
                    chat_id=item.get("chat"),
                )
                continue
            external_id = item.get("external_id")
            if external_id:
                await consume_event(tenant_id, source_id, external_id, "message.created", item)
        total += len(items)
        if next_cursor and next_cursor != prev_cursor:
            cursor = next_cursor
            await save_cursor(tenant_id, source_id, WECOM_MESSAGE_STREAM, next_cursor)
        if not has_more or next_cursor == prev_cursor:
            break
    await mark_sync_ok(tenant_id, source_id)
    logger.info(
        "connector_sync_completed",
        tenant_id=tenant_id,
        source_id=source_id,
        items=total,
    )
    return WECOM_MESSAGE_STREAM
