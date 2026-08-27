"""Token ledger tests (Phase 7).

Locks: one settlement per (tenant, request_id), reserve→settle transition,
REFUNDED stays untouched by later settles, and ledger failures never break
the caller.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models import infra as infra_models
from app.data.models.base import Base
from app.shared.token_ledger import (
    TokenLedgerEntry,
    persist_ledger_entry,
    settle_reservation,
)


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import asyncio

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, tables=[infra_models.TokenLedgerEntry.__table__])
            )

    asyncio.run(make())
    yield factory
    import asyncio

    asyncio.run(engine.dispose())


async def test_double_settle_returns_same_entry(session_factory):
    async with session_factory() as session:
        first = await persist_ledger_entry(session, "t1", "req-1", 100)
        await session.commit()
        second = await persist_ledger_entry(session, "t1", "req-1", 100)
        rows = (await session.execute(select(TokenLedgerEntry))).scalars().all()
        assert len(rows) == 1
        assert first.id == second.id


async def test_settle_reserves_then_settles_once(session_factory):
    async with session_factory() as session:
        reserve = await persist_ledger_entry(session, "t1", "req-2", 500, settlement_state="RESERVE")
        assert reserve.settlement_state == "RESERVE"
        await session.commit()

        settled = await settle_reservation(session, "t1", "req-2", actual_total=320, input_tokens=200, output_tokens=120)
        await session.commit()
        assert settled.total_tokens == 320
        assert settled.measured is True

        again = await settle_reservation(session, "t1", "req-2", actual_total=999)
        await session.commit()
        assert again.total_tokens == 320  # no double settlement


async def test_refunded_entry_not_reset_by_settle(session_factory):
    async with session_factory() as session:
        await persist_ledger_entry(session, "t1", "req-3", 50, settlement_state="REFUNDED")
        await session.commit()
        result = await settle_reservation(session, "t1", "req-3", actual_total=500)
        await session.commit()
        assert result.total_tokens == 50  # refund preserved


async def test_ledger_failure_returns_none_not_raise(session_factory):
    async with session_factory() as session:
        # Simulate a broken write path: point the model at a nonexistent table.
        original = infra_models.TokenLedgerEntry.__table__
        session.add = None  # type: ignore[method-assign]
        result = await persist_ledger_entry(session, "t1", "req-4", 10)
        assert result is None  # never raises
        del session.add
        _ = original
