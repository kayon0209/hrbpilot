"""Operations console: reconciliation of UNKNOWN outcomes + DLQ (§4.1).

Before this, an execution whose outcome could not be determined (the external
system did not answer conclusively) was only visible in logs — reconciling one
meant grepping log files, and nothing could be done about it from the product.
The dead-letter queue had a replay routine but no way to see it or drive it.

Both are now queryable and actionable behind a single admin capability
(``ops_reconciliation``), and every mutation writes an audit row inside the
same transaction as the business change.

Design note: replay does NOT execute the tool from the API process. It
re-queues the message so a worker picks it up — keeping execution in the
worker and the API process free of tool side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.access.middleware.decorators import require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_session_factory
from app.data.models.hr_case import ToolExecution
from app.data.models.runtime import OutboxMessage
from app.shared.audit import append_security_audit_event
from app.shared.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/ops", tags=["ops-reconciliation"])

# An execution the dispatcher could not resolve: the external outcome is
# genuinely unknown, so a human must reconcile it — never auto-guessed.
UNKNOWN_STATUS = "UNKNOWN"
# Terminal states a human may confirm when reconciling.
RECONCILED_SUCCESS = "SUCCEEDED"
RECONCILED_FAILED = "FAILED"
DEAD_LETTER_STATUS = "dead_letter"
PENDING_STATUS = "pending"
TERMINAL_STATUS = "terminal"

MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    """Keep the page size bounded — an unbounded limit is a cheap DoS."""
    if limit <= 0:
        raise ValidationError("limit 必须为正整数")
    return min(limit, MAX_LIMIT)


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", "unknown")


def _execution_row(row: ToolExecution) -> dict:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "tool_name": row.tool_name,
        "status": row.status,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "request_id": row.request_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _message_row(row: OutboxMessage) -> dict:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "queue_name": row.queue_name,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "next_attempt_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "last_error_code": row.last_error_code,
    }


@router.get("/unknown-executions")
@require_capability("ops_reconciliation")
async def list_unknown_executions(request: Request, limit: int = 50) -> dict:
    """Executions whose outcome could not be determined and need reconciling."""
    tenant_id = require_tenant_id(request)
    page = _clamp_limit(limit)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(ToolExecution)
                    .where(ToolExecution.tenant_id == tenant_id, ToolExecution.status == UNKNOWN_STATUS)
                    .order_by(ToolExecution.updated_at.desc())
                    .limit(page)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_execution_row(row) for row in rows], "count": len(rows)}


class ResolveUnknownBody(BaseModel):
    """Human decision on an UNKNOWN execution, with an auditable reason."""

    resolution: str = Field(pattern=f"^({RECONCILED_SUCCESS}|{RECONCILED_FAILED})$")
    note: str = Field(default="", max_length=500)


@router.post("/unknown-executions/{execution_id}/resolve")
@require_capability("ops_reconciliation")
async def resolve_unknown_execution(
    execution_id: str,
    body: ResolveUnknownBody,
    request: Request,
) -> dict:
    """Reconcile one UNKNOWN execution: confirm what actually happened.

    The operator supplies the verdict and a reason; both are written to the
    audit log in the same transaction, so the decision is always traceable.
    """
    tenant_id = require_tenant_id(request)
    actor_id = _actor(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(ToolExecution).where(
                ToolExecution.id == execution_id,
                ToolExecution.tenant_id == tenant_id,
                ToolExecution.status == UNKNOWN_STATUS,
            )
        )
        if row is None:
            raise NotFoundError("ToolExecution", execution_id)
        row.status = body.resolution
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="reconcile_unknown_execution",
            object_type="tool_execution",
            object_id=execution_id,
            details={"resolution": body.resolution, "note": body.note, "tool": row.tool_name},
        )
        await db.commit()
    return {"id": execution_id, "status": body.resolution, "reconciled": True}


@router.get("/dlq")
@require_capability("ops_reconciliation")
async def list_dead_letter(request: Request, limit: int = 50) -> dict:
    """Messages that exhausted retries and are waiting for a human decision."""
    tenant_id = require_tenant_id(request)
    page = _clamp_limit(limit)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(OutboxMessage)
                    .where(OutboxMessage.tenant_id == tenant_id, OutboxMessage.status == DEAD_LETTER_STATUS)
                    .order_by(OutboxMessage.next_attempt_at.asc())
                    .limit(page)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_message_row(row) for row in rows], "count": len(rows)}


@router.post("/dlq/{message_id}/replay")
@require_capability("ops_reconciliation")
async def replay_dead_letter(message_id: str, request: Request) -> dict:
    """Re-queue a dead-lettered message so a worker retries it.

    Deliberately does not execute the tool here: the API process stays free of
    tool side effects and the retry inherits the worker's normal retry/lease
    semantics.
    """
    tenant_id = require_tenant_id(request)
    actor_id = _actor(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(OutboxMessage).where(
                OutboxMessage.id == message_id,
                OutboxMessage.tenant_id == tenant_id,
                OutboxMessage.status == DEAD_LETTER_STATUS,
            )
        )
        if row is None:
            raise NotFoundError("OutboxMessage", message_id)
        row.status = PENDING_STATUS
        row.attempt_count = 0
        row.next_attempt_at = datetime.now(UTC)
        row.lease_owner = None
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="dlq_replay",
            object_type="outbox_message",
            object_id=message_id,
            details={"event_type": row.event_type},
        )
        await db.commit()
    return {"id": message_id, "status": PENDING_STATUS, "requeued": True}


@router.post("/dlq/{message_id}/discard")
@require_capability("ops_reconciliation")
async def discard_dead_letter(message_id: str, request: Request) -> dict:
    """Give up on a message: move it to terminal so it is never delivered."""
    tenant_id = require_tenant_id(request)
    actor_id = _actor(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(OutboxMessage).where(
                OutboxMessage.id == message_id,
                OutboxMessage.tenant_id == tenant_id,
                OutboxMessage.status == DEAD_LETTER_STATUS,
            )
        )
        if row is None:
            raise NotFoundError("OutboxMessage", message_id)
        row.status = TERMINAL_STATUS
        row.lease_owner = None
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="dlq_discard",
            object_type="outbox_message",
            object_id=message_id,
            details={"event_type": row.event_type},
        )
        await db.commit()
    return {"id": message_id, "status": TERMINAL_STATUS, "discarded": True}
