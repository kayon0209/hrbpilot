"""Token ledger persistence (Phase 7).

Redis keeps hot monthly aggregates and alert thresholds (unchanged); the
PostgreSQL ``token_ledger`` table is the traceable, append-only record.
``record_token_usage`` keeps its exact signature — ledger writes are
best-effort and never break the caller; a failed ledger write is logged.

settlement: each (tenant_id, request_id) settles once (DB unique constraint);
duplicate settle calls return the existing entry instead of double-counting.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.infra import TokenLedgerEntry
from app.shared.logger import get_logger

logger = get_logger(__name__)


async def persist_ledger_entry(
    session: AsyncSession,
    tenant_id: str,
    request_id: str,
    total_tokens: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "unknown",
    scenario_id: str = "unknown",
    agent_run_id: str | None = None,
    measured: bool = False,
    settlement_state: str = "SETTLED",
) -> TokenLedgerEntry | None:
    """Append one ledger row; idempotent per (tenant, request_id).

    Returns the existing entry when the request_id was already settled.
    Raises nothing: ledger failures must not break the caller's request —
    callers that own a transaction commit; this function only flushes.
    """
    try:
        existing = (
            await session.execute(
                select(TokenLedgerEntry).where(
                    TokenLedgerEntry.tenant_id == tenant_id,
                    TokenLedgerEntry.request_id == request_id,
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing

        entry = TokenLedgerEntry(
            tenant_id=tenant_id,
            request_id=request_id,
            agent_run_id=agent_run_id,
            scenario_id=scenario_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            measured=measured,
            settlement_state=settlement_state,
        )
        session.add(entry)
        await session.flush()
        return entry
    except Exception as e:
        logger.warning("token_ledger_write_failed", error=str(e), tenant_id=tenant_id, request_id=request_id)
        return None


async def settle_reservation(
    session: AsyncSession,
    tenant_id: str,
    request_id: str,
    actual_total: int,
    *,
    measured: bool = True,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> TokenLedgerEntry | None:
    """Settle a RESERVE row to SETTLED with actual usage (idempotent)."""
    try:
        entry = (
            await session.execute(
                select(TokenLedgerEntry).where(
                    TokenLedgerEntry.tenant_id == tenant_id,
                    TokenLedgerEntry.request_id == request_id,
                )
            )
        ).scalars().first()
        if entry is None:
            return await persist_ledger_entry(
                session, tenant_id, request_id, actual_total,
                input_tokens=input_tokens, output_tokens=output_tokens, measured=measured,
            )
        if entry.settlement_state == "SETTLED":
            return entry  # already settled — no double settlement
        if entry.settlement_state == "REFUNDED":
            return entry  # refunds are final; settlement must not overwrite
        entry.total_tokens = actual_total
        entry.input_tokens = input_tokens
        entry.output_tokens = output_tokens
        entry.measured = measured
        entry.settlement_state = "SETTLED"
        await session.flush()
        return entry
    except Exception as e:
        logger.warning("token_ledger_settle_failed", error=str(e), request_id=request_id)
        return None


def new_request_id() -> str:
    return uuid.uuid4().hex


def ledger_month_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m")
