"""Connector sync engine: cursors, idempotent events, rate limiting.

The three guarantees a Level 2 certification needs (capability matrix 安全基线):

1. Incremental: per-(source, stream) cursors resume syncs where they stopped —
   a crashed or paused run never re-fetches the whole stream silently.
2. Idempotent: every consumed external event is recorded in
   connector_event_log with a (tenant, source, external_event_id) UNIQUE
   index. A redelivered webhook increments replay_count and returns
   already_seen=True — the side effect is NOT re-triggered.
3. Bounded: a token-bucket limiter caps requests per platform per minute so
   a misbehaving sync cannot hammer a provider (or trip a provider-side ban).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.data.database import get_session_factory
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)


class SyncPausedError(AppError):
    def __init__(self) -> None:
        super().__init__("数据源已暂停，同步未执行", code="CONFLICT", status_code=409)


async def get_cursor(tenant_id: str, source_id: str, stream: str) -> str | None:
    from app.data.models.connector import ConnectorSyncCursor

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(ConnectorSyncCursor).where(
                ConnectorSyncCursor.tenant_id == tenant_id,
                ConnectorSyncCursor.source_id == source_id,
                ConnectorSyncCursor.stream == stream,
            )
        )
    return row.cursor if row else None


async def save_cursor(tenant_id: str, source_id: str, stream: str, cursor: str) -> None:
    from sqlalchemy.dialects import postgresql

    from app.data.models.connector import ConnectorSyncCursor

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(
            postgresql.insert(ConnectorSyncCursor)
            .values(
                tenant_id=tenant_id,
                source_id=source_id,
                stream=stream,
                cursor=cursor,
            )
            .on_conflict_do_update(
                constraint="uq_connector_cursor_scope",
                set_={"cursor": cursor, "synced_at": datetime.now(UTC), "updated_at": datetime.now(UTC)},
            )
        )
        await db.commit()


def payload_digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _external_sender(payload: dict) -> str | None:
    """Read a sender identifier without guessing an internal identity."""
    sender = payload.get("sender") or payload.get("FromUserName")
    if isinstance(sender, dict):
        sender = sender.get("userid") or sender.get("user_id") or sender.get("open_id")
    if sender:
        return str(sender).strip() or None

    event_sender = payload.get("event", {}).get("sender", {}) if isinstance(payload.get("event"), dict) else {}
    sender_id = event_sender.get("sender_id", {}) if isinstance(event_sender, dict) else {}
    if isinstance(sender_id, dict):
        sender = sender_id.get("user_id") or sender_id.get("open_id")
    return str(sender).strip() if sender else None


def _message_content(payload: dict) -> str | None:
    """Extract human-entered text from the normalized or provider callback body."""
    content = payload.get("content") or payload.get("Content")
    if content is None and isinstance(payload.get("event"), dict):
        message = payload["event"].get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        # Feishu's message content is often a JSON string such as
        # {"text":"..."}; preserve only the text field when present.
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            content = parsed["text"]
    return str(content).strip() if content is not None and str(content).strip() else None


async def consume_event(
    tenant_id: str,
    source_id: str,
    external_event_id: str,
    event_type: str,
    payload: dict,
) -> bool:
    """Claim one event and materialize an opt-in HR request atomically.

    Replays only increment the durable counter.  For a source explicitly
    configured as an employee-request entry, the identity binding, request
    insert, and terminal event status all commit together; a crash cannot
    leave a request whose source event still looks incomplete.
    """
    from sqlalchemy.dialects import postgresql

    from app.data.models.connector import (
        ConnectorEventLog,
        ConnectorIdentityBinding,
        ConnectorIntakeEvent,
    )
    from app.data.models.data_source import DataSource
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        result = await db.execute(
            postgresql.insert(ConnectorEventLog)
            .values(
                tenant_id=tenant_id,
                source_id=source_id,
                external_event_id=external_event_id,
                event_type=event_type,
                payload_digest=payload_digest(payload),
                status="processing",
                processing_started_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                constraint="uq_connector_event_consumed",
                set_={"replay_count": ConnectorEventLog.__table__.c.replay_count + 1},
            )
            .returning(ConnectorEventLog.replay_count)
        )
        replay_count = result.scalar_one()
        if replay_count == 0:
            source = await db.scalar(
                select(DataSource)
                .where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                .with_for_update()
            )
            if source is not None and source.event_route == "employee_request":
                sender = _external_sender(payload)
                content = _message_content(payload)
                if not sender or not content:
                    await db.execute(
                        update(ConnectorEventLog)
                        .where(
                            ConnectorEventLog.tenant_id == tenant_id,
                            ConnectorEventLog.source_id == source_id,
                            ConnectorEventLog.external_event_id == external_event_id,
                            ConnectorEventLog.status == "processing",
                        )
                        .values(
                            status="failed",
                            failed_at=datetime.now(UTC),
                            last_error="员工请求入口缺少发送者或消息正文",
                        )
                    )
                else:
                    binding = await db.scalar(
                        select(ConnectorIdentityBinding).where(
                            ConnectorIdentityBinding.tenant_id == tenant_id,
                            ConnectorIdentityBinding.source_id == source_id,
                            ConnectorIdentityBinding.external_user_id == sender,
                        )
                    )
                    if binding is None:
                        db.add(
                            ConnectorIntakeEvent(
                                tenant_id=tenant_id,
                                source_id=source_id,
                                external_event_id=external_event_id,
                                external_user_id=sender,
                                title="来自企业协作平台的待确认员工请求",
                                description=content,
                                status="pending_identity",
                            )
                        )
                        await db.execute(
                            update(ConnectorEventLog)
                            .where(
                                ConnectorEventLog.tenant_id == tenant_id,
                                ConnectorEventLog.source_id == source_id,
                                ConnectorEventLog.external_event_id == external_event_id,
                                ConnectorEventLog.status == "processing",
                            )
                            .values(status="processed", processed_at=datetime.now(UTC), last_error=None)
                        )
                    else:
                        db.add(
                            EmployeeRequest(
                                tenant_id=tenant_id,
                                created_by=binding.user_id,
                                request_type="other",
                                title="来自企业协作平台的员工请求",
                                description=content,
                                status="submitted",
                                next_step_for_employee="HR 会尽快处理；如需补充材料会在这里说明。",
                                connector_source_id=source_id,
                                connector_external_event_id=external_event_id,
                                external_sender_id=sender,
                            )
                        )
                        await db.execute(
                            update(ConnectorEventLog)
                            .where(
                                ConnectorEventLog.tenant_id == tenant_id,
                                ConnectorEventLog.source_id == source_id,
                                ConnectorEventLog.external_event_id == external_event_id,
                                ConnectorEventLog.status == "processing",
                            )
                            .values(
                                status="processed",
                                processed_at=datetime.now(UTC),
                                last_error=None,
                            )
                        )
        await db.commit()
    return replay_count == 0


async def mark_event_processed(tenant_id: str, source_id: str, external_event_id: str) -> bool:
    """Mark an event complete after its downstream business side effect commits.

    A stale or replayed completion cannot overwrite a failed or already
    completed event.  The worker which owns the real destination must call
    this after (not before) that destination is durable.
    """
    from sqlalchemy import update

    from app.data.models.connector import ConnectorEventLog

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        result = await db.execute(
            update(ConnectorEventLog)
            .where(
                ConnectorEventLog.tenant_id == tenant_id,
                ConnectorEventLog.source_id == source_id,
                ConnectorEventLog.external_event_id == external_event_id,
                ConnectorEventLog.status == "processing",
            )
            .values(status="processed", processed_at=datetime.now(UTC), last_error=None)
            .returning(ConnectorEventLog.id)
        )
        completed_id = result.scalar_one_or_none()
        await db.commit()
    return completed_id is not None


async def mark_event_failed(tenant_id: str, source_id: str, external_event_id: str, error: str) -> bool:
    """Persist a downstream processing failure without falsely completing it."""
    from sqlalchemy import update

    from app.data.models.connector import ConnectorEventLog

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        result = await db.execute(
            update(ConnectorEventLog)
            .where(
                ConnectorEventLog.tenant_id == tenant_id,
                ConnectorEventLog.source_id == source_id,
                ConnectorEventLog.external_event_id == external_event_id,
                ConnectorEventLog.status == "processing",
            )
            .values(status="failed", failed_at=datetime.now(UTC), last_error=error[:500])
            .returning(ConnectorEventLog.id)
        )
        failed_id = result.scalar_one_or_none()
        await db.commit()
    return failed_id is not None


@dataclass
class TokenBucket:
    """Sliding-window request limiter (per platform, per minute)."""

    max_per_minute: int
    _hits: list[float] = field(default_factory=list)

    def check(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        window_start = now - 60.0
        self._hits = [ts for ts in self._hits if ts >= window_start]
        if len(self._hits) >= self.max_per_minute:
            raise AppError(
                f"连接器请求超过每分钟 {self.max_per_minute} 次上限，已限流",
                code="RATE_LIMITED",
                status_code=429,
            )
        self._hits.append(now)


_BUCKETS: dict[str, TokenBucket] = {}


def limiter_for(key: str, max_per_minute: int = 120) -> TokenBucket:
    """Per-process limiter keyed by ``platform:tenant:source`` (CONN-05).

    A single source cannot exhaust another source's budget.  Multi-worker
    coordination is a deploy-time concern (each worker has its own bucket);
    this bounds what a single in-process sync loop can do to the provider.
    """
    if key not in _BUCKETS:
        _BUCKETS[key] = TokenBucket(max_per_minute=max_per_minute)
    return _BUCKETS[key]


async def mark_sync_failed(tenant_id: str, source_id: str, error: str) -> None:
    from app.data.models.data_source import DataSource

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        # Only a live (not exploded/revoked/paused-after-start) source records a
        # failed sync; a revoked source stays revoked and never shows "failed".
        await db.execute(
            update(DataSource)
            .where(
                DataSource.tenant_id == tenant_id,
                DataSource.id == source_id,
                DataSource.revoked_at.is_(None),
            )
            .values(sync_status="failed", last_error=error[:500])
        )
        await db.commit()
    logger.warning("connector_sync_failed", tenant_id=tenant_id, source_id=source_id, error=error[:200])


async def mark_sync_ok(tenant_id: str, source_id: str) -> None:
    from app.data.models.data_source import DataSource

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        # A source that was paused or revoked while the sync was in flight must
        # NOT be flipped back to OK by a stale sync finishing late. The write
        # is conditional so the revoke/pause always wins.
        await db.execute(
            update(DataSource)
            .where(
                DataSource.tenant_id == tenant_id,
                DataSource.id == source_id,
                DataSource.revoked_at.is_(None),
                DataSource.paused.is_(False),
            )
            .values(
                sync_status="ok",
                last_sync_at=datetime.now(UTC),
                last_error=None,
            )
        )
        await db.commit()
