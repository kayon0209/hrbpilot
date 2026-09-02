"""Sync engine contract: cursors, idempotent events, replay counting, limits.

These exercise the idempotent event/cursor persistence contract, which needs a
live PostgreSQL with the connector tables (migration 018) — hence integration.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.connectors.sync import (
    TokenBucket,
    consume_event,
    get_cursor,
    limiter_for,
    payload_digest,
    save_cursor,
)
from app.data.database import get_session_factory
from app.data.models.connector import (
    ConnectorEventLog,
    ConnectorIdentityBinding,
    ConnectorIntakeEvent,
    ConnectorSyncCursor,
)
from app.data.models.data_source import DataSource
from app.data.models.scenarios import EmployeeRequest
from app.data.models.user import User
from app.scenarios.data_source.service import bind_platform_identity
from app.shared.errors import AppError

pytestmark = pytest.mark.integration


async def _seed_source(tenant_id: str, source_id: str, *, event_route: str = "none") -> str:
    """The cursors/event tables carry FKs to data_sources — a real parent row
    is part of the contract (DB-level integrity for connector data)."""
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            DataSource(
                id=source_id,
                tenant_id=tenant_id,
                name="连接器测试数据源",
                platform="wecom",
                purpose="验收测试",
                authorized_scope="测试范围",
                content_types='["messages"]',
                data_destination="测试工作区",
                event_route=event_route,
                created_by="connector-test",
            )
        )
        await db.commit()
    return source_id


async def _cleanup(tenant_id: str, source_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
        await db.execute(delete(ConnectorIntakeEvent).where(ConnectorIntakeEvent.tenant_id == tenant_id))
        await db.execute(delete(ConnectorIdentityBinding).where(ConnectorIdentityBinding.tenant_id == tenant_id))
        await db.execute(
            delete(ConnectorEventLog).where(
                ConnectorEventLog.tenant_id == tenant_id, ConnectorEventLog.source_id == source_id
            )
        )
        await db.execute(
            delete(ConnectorSyncCursor).where(
                ConnectorSyncCursor.tenant_id == tenant_id, ConnectorSyncCursor.source_id == source_id
            )
        )
        await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


@pytest.mark.asyncio
async def test_cursor_roundtrip_resumes_where_it_stopped() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    await _seed_source(tenant_id, source_id)
    try:
        assert await get_cursor(tenant_id, source_id, "messages") is None
        await save_cursor(tenant_id, source_id, "messages", "seq-100")
        assert await get_cursor(tenant_id, source_id, "messages") == "seq-100"
        # advancing is an upsert, not a duplicate row
        await save_cursor(tenant_id, source_id, "messages", "seq-250")
        assert await get_cursor(tenant_id, source_id, "messages") == "seq-250"
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_event_consumed_exactly_once_replay_increments() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    await _seed_source(tenant_id, source_id)
    try:
        first = await consume_event(tenant_id, source_id, "evt-1", "message.created", {"id": "evt-1"})
        assert first is True, "first delivery must trigger the side effect"

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            from sqlalchemy import select

            row = (
                (await db.execute(select(ConnectorEventLog).where(ConnectorEventLog.external_event_id == "evt-1")))
                .scalars()
                .first()
            )
        # Recording an inbound delivery is not its business side effect.  A
        # newly claimed event must therefore stay processing until that effect
        # has durably succeeded; it must never be born "processed".
        assert getattr(row, "status", None) == "processing"
        assert row.processed_at is None

        replay = await consume_event(tenant_id, source_id, "evt-1", "message.created", {"id": "evt-1"})
        assert replay is False, "redelivery must NOT re-trigger the side effect"

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            from sqlalchemy import select

            row = (
                (await db.execute(select(ConnectorEventLog).where(ConnectorEventLog.external_event_id == "evt-1")))
                .scalars()
                .first()
            )
        assert row.replay_count == 1
        # A replay cannot turn an unfinished event into a false success.
        assert getattr(row, "status", None) in {"processing", "replayed"}
        assert row.processed_at is None
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_event_log_is_tenant_scoped() -> None:
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    source_a, source_b = str(uuid4()), str(uuid4())
    await _seed_source(tenant_a, source_a)
    await _seed_source(tenant_b, source_b)
    try:
        await consume_event(tenant_a, source_a, "evt-shared", "doc.updated", {"x": 1})
        # The same external event id under another tenant is a DIFFERENT event.
        first_for_b = await consume_event(tenant_b, source_b, "evt-shared", "doc.updated", {"x": 1})
        assert first_for_b is True
    finally:
        await _cleanup(tenant_a, source_a)
        await _cleanup(tenant_b, source_b)


@pytest.mark.asyncio
async def test_bound_employee_message_creates_one_employee_request_and_completes_event() -> None:
    tenant_id, source_id, employee_id = str(uuid4()), str(uuid4()), str(uuid4())
    external_user_id = "wecom-employee-10086"
    await _seed_source(tenant_id, source_id, event_route="employee_request")
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="test-only",
                    role="employee",
                )
            )
            await db.flush()
            db.add(
                ConnectorIdentityBinding(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    external_user_id=external_user_id,
                    user_id=employee_id,
                    created_by="connector-admin",
                )
            )
            await db.commit()

        first = await consume_event(
            tenant_id,
            source_id,
            "msg-10086",
            "message.created",
            {"sender": external_user_id, "content": "请帮我开具在职证明", "chat": "chat-hr"},
        )
        replay = await consume_event(
            tenant_id,
            source_id,
            "msg-10086",
            "message.created",
            {"sender": external_user_id, "content": "请帮我开具在职证明", "chat": "chat-hr"},
        )
        assert first is True
        assert replay is False

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            requests = list(
                (await db.execute(select(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))).scalars()
            )
            event = await db.scalar(
                select(ConnectorEventLog).where(
                    ConnectorEventLog.tenant_id == tenant_id,
                    ConnectorEventLog.source_id == source_id,
                    ConnectorEventLog.external_event_id == "msg-10086",
                )
            )
        assert len(requests) == 1
        assert requests[0].created_by == employee_id
        assert requests[0].status == "submitted"
        assert requests[0].connector_source_id == source_id
        assert requests[0].connector_external_event_id == "msg-10086"
        assert requests[0].external_sender_id == external_user_id
        assert event is not None and event.status == "processed"
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_unbound_employee_message_is_accepted_without_creating_a_request() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    await _seed_source(tenant_id, source_id, event_route="employee_request")
    factory = get_session_factory()
    try:
        first = await consume_event(
            tenant_id,
            source_id,
            "msg-unbound-1",
            "message.created",
            {"sender": "wecom-unbound", "content": "我的休假流程卡住了", "chat": "chat-hr"},
        )
        assert first is True
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            requests = list(
                (await db.execute(select(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))).scalars()
            )
            event = await db.scalar(
                select(ConnectorEventLog).where(
                    ConnectorEventLog.tenant_id == tenant_id,
                    ConnectorEventLog.source_id == source_id,
                    ConnectorEventLog.external_event_id == "msg-unbound-1",
                )
            )
            intake = await db.scalar(
                select(ConnectorIntakeEvent).where(
                    ConnectorIntakeEvent.tenant_id == tenant_id,
                    ConnectorIntakeEvent.source_id == source_id,
                    ConnectorIntakeEvent.external_event_id == "msg-unbound-1",
                )
            )
        assert requests == []
        assert event is not None and event.status == "processed"
        assert intake is not None and intake.status == "pending_identity"
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_binding_materializes_pending_employee_request_once() -> None:
    tenant_id, source_id, employee_id = str(uuid4()), str(uuid4()), str(uuid4())
    external_user_id = "wecom-late-bound"
    await _seed_source(tenant_id, source_id, event_route="employee_request")
    factory = get_session_factory()
    try:
        await consume_event(
            tenant_id,
            source_id,
            "msg-late-bound-1",
            "message.created",
            {"sender": external_user_id, "content": "我的报销流程需要协助", "chat": "chat-hr"},
        )
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Late bound employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="test-only",
                    role="employee",
                )
            )
            await db.commit()

        await bind_platform_identity(tenant_id, "connector-admin", source_id, external_user_id, employee_id)
        # A repeated administrator save must be an upsert, not a second request.
        await bind_platform_identity(tenant_id, "connector-admin", source_id, external_user_id, employee_id)

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            requests = list(
                (await db.execute(select(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))).scalars()
            )
            intake = await db.scalar(
                select(ConnectorIntakeEvent).where(
                    ConnectorIntakeEvent.tenant_id == tenant_id,
                    ConnectorIntakeEvent.source_id == source_id,
                    ConnectorIntakeEvent.external_event_id == "msg-late-bound-1",
                )
            )
        assert len(requests) == 1
        assert requests[0].created_by == employee_id
        assert requests[0].connector_external_event_id == "msg-late-bound-1"
        assert intake is not None and intake.status == "materialized"
        assert intake.employee_request_id == requests[0].id
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_concurrent_bound_message_deliveries_create_one_employee_request() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")

    tenant_id, source_id, employee_id = str(uuid4()), str(uuid4()), str(uuid4())
    external_user_id = "wecom-concurrent-employee"
    await _seed_source(tenant_id, source_id, event_route="employee_request")
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Concurrent employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="test-only",
                    role="employee",
                )
            )
            await db.flush()
            db.add(
                ConnectorIdentityBinding(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    external_user_id=external_user_id,
                    user_id=employee_id,
                    created_by="connector-admin",
                )
            )
            await db.commit()

        payload = {"sender": external_user_id, "content": "我需要确认社保缴纳情况", "chat": "chat-hr"}
        results = await asyncio.gather(
            *(consume_event(tenant_id, source_id, "msg-concurrent-1", "message.created", payload) for _ in range(8))
        )
        assert sum(results) == 1
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            request_count = len(
                list(
                    (await db.execute(select(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))).scalars()
                )
            )
        assert request_count == 1
    finally:
        await _cleanup(tenant_id, source_id)


@pytest.mark.asyncio
async def test_identity_binding_is_invisible_to_another_tenant_via_rls() -> None:
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    source_id, employee_id = str(uuid4()), str(uuid4())
    await _seed_source(tenant_a, source_id, event_route="employee_request")
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_a
            db.add(
                User(
                    id=employee_id,
                    tenant_id=tenant_a,
                    name="Tenant A employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="test-only",
                    role="employee",
                )
            )
            await db.flush()
            db.add(
                ConnectorIdentityBinding(
                    tenant_id=tenant_a,
                    source_id=source_id,
                    external_user_id="wecom-tenant-a",
                    user_id=employee_id,
                    created_by="connector-admin",
                )
            )
            await db.commit()

        async with factory() as db:
            db.info["tenant_id"] = tenant_b
            visible = await db.scalar(
                select(ConnectorIdentityBinding).where(
                    ConnectorIdentityBinding.tenant_id == tenant_a,
                    ConnectorIdentityBinding.source_id == source_id,
                )
            )
        assert visible is None
    finally:
        await _cleanup(tenant_a, source_id)


def test_payload_digest_is_canonical_regardless_of_key_order() -> None:
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})


def test_token_bucket_limits_requests_per_minute() -> None:
    bucket = TokenBucket(max_per_minute=3)
    bucket.check(now=0.0)
    bucket.check(now=0.1)
    bucket.check(now=0.2)
    with pytest.raises(AppError) as exc_info:
        bucket.check(now=0.3)
    assert "限流" in str(exc_info.value)
    # after the 60s window slides, requests pass again
    bucket.check(now=61.0)


def test_limiter_for_returns_per_source_bucket() -> None:
    assert limiter_for("feishu:t1:s1") is limiter_for("feishu:t1:s1")
    assert limiter_for("feishu:t1:s1") is not limiter_for("wecom:t1:s1")
    # CONN-05: different sources share no budget.
    assert limiter_for("wecom:t1:s1") is not limiter_for("wecom:t1:s2")
