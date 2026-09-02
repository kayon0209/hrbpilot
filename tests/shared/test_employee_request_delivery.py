"""PostgreSQL acceptance tests for local connector delivery attempts.

The database itself must protect the outbox's business idempotency and tenant
boundaries.  Raw SQL deliberately bypasses the service layer and its ACL
filters, so a green result is evidence for migration 030's constraints.
"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from app.data.database import make_tenant_session


pytestmark = pytest.mark.integration


def _require_db_security_tests() -> None:
    if not os.environ.get("HRBP_RUN_DB_RLS_TESTS") and not os.environ.get(
        "HRBP_RUN_CONCURRENCY_TESTS"
    ):
        pytest.skip("set HRBP_RUN_DB_RLS_TESTS=true for isolated PostgreSQL delivery verification")


async def _expect_constraint_violation(session, statement: str, params: dict[str, str]) -> None:
    with pytest.raises(Exception) as exc_info:
        await session.execute(text(statement), params)
    message = str(exc_info.value).lower()
    cause = exc_info.value.__cause__
    assert (
        "violates" in message
        or (cause is not None and "Violation" in type(cause).__name__)
    ), f"expected a database constraint violation, got {exc_info.value}"


async def _seed_request_and_source(session, tenant_id: str, user_id: str) -> tuple[str, str]:
    source_id, request_id = str(uuid4()), str(uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, name, email, hashed_password, role) "
            "VALUES (:id, :tenant_id, 'Delivery HR', :email, 'x', 'hr_manager')"
        ),
        {"id": user_id, "tenant_id": tenant_id, "email": f"{user_id}@delivery.invalid"},
    )
    await session.execute(
        text(
            "INSERT INTO data_sources (id, tenant_id, name, platform, purpose, authorized_scope, "
            "content_types, data_destination, event_route, created_by, oauth_state) "
            "VALUES (:id, :tenant_id, 'WeCom delivery', 'wecom', 'p', 's', '[]', 'd', "
            "'employee_request', :user_id, 'none')"
        ),
        {"id": source_id, "tenant_id": tenant_id, "user_id": user_id},
    )
    await session.execute(
        text(
            "INSERT INTO employee_requests (id, tenant_id, created_by, request_type, title, description, status) "
            "VALUES (:id, :tenant_id, :user_id, 'other', 'Delivery request', 'x', 'submitted')"
        ),
        {"id": request_id, "tenant_id": tenant_id, "user_id": user_id},
    )
    await session.commit()
    return request_id, source_id


def _attempt_sql() -> str:
    return (
        "INSERT INTO connector_delivery_attempts "
        "(id, tenant_id, employee_request_id, source_id, channel, recipient_ref, message_content, content_digest, status) "
        "VALUES (:id, :tenant_id, :request_id, :source_id, 'wecom_simulator', "
        "'employee-a', '请补充材料', :digest, 'queued')"
    )


@pytest.mark.asyncio
async def test_delivery_attempt_rejects_duplicate_business_version_and_cross_tenant_bindings() -> None:
    _require_db_security_tests()
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    session_a = await make_tenant_session(tenant_a)
    session_b = None
    try:
        table = await session_a.scalar(text("SELECT to_regclass('public.connector_delivery_attempts')"))
        assert table == "connector_delivery_attempts", "migration 030 must create the delivery outbox table"
        session_b = await make_tenant_session(tenant_b)
        request_a, source_a = await _seed_request_and_source(session_a, tenant_a, str(uuid4()))
        _request_b, source_b = await _seed_request_and_source(session_b, tenant_b, str(uuid4()))

        first = {
            "id": str(uuid4()),
            "tenant_id": tenant_a,
            "request_id": request_a,
            "source_id": source_a,
            "digest": "a" * 64,
        }
        await session_a.execute(text(_attempt_sql()), first)
        await session_a.commit()

        await _expect_constraint_violation(
            session_a,
            _attempt_sql(),
            {**first, "id": str(uuid4())},
        )
        await session_a.rollback()

        await _expect_constraint_violation(
            session_b,
            _attempt_sql(),
            {
                "id": str(uuid4()),
                "tenant_id": tenant_b,
                "request_id": request_a,
                "source_id": source_b,
                "digest": "b" * 64,
            },
        )
        await session_b.rollback()
    finally:
        await session_a.close()
        if session_b is not None:
            await session_b.close()


@pytest.mark.asyncio
async def test_triage_commits_request_and_one_delivery_snapshot_without_internal_note() -> None:
    _require_db_security_tests()
    from datetime import UTC, datetime

    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt, ConnectorEventLog
    from app.data.models.data_source import DataSource
    from app.data.models.infra import AuditLog
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.scenarios.employee_request.service import HrTriageBody, hr_triage

    tenant_id = str(uuid4())
    hr_id, employee_id, source_id, request_id = [str(uuid4()) for _ in range(4)]
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add_all(
                [
                    User(id=hr_id, tenant_id=tenant_id, name="HR", email=f"{hr_id}@delivery.invalid", hashed_password="x", role="hrbp"),
                    User(id=employee_id, tenant_id=tenant_id, name="Employee", email=f"{employee_id}@delivery.invalid", hashed_password="x", role="employee"),
                    DataSource(
                        id=source_id,
                        tenant_id=tenant_id,
                        name="WeCom intake",
                        platform="wecom",
                        purpose="employee request",
                        authorized_scope="direct messages",
                        content_types="[]",
                        data_destination="employee requests",
                        event_route="employee_request",
                        created_by=hr_id,
                        wecom_callback_config_encrypted=b"configured-local-simulator",
                    ),
                ]
            )
            await db.flush()
            db.add(
                ConnectorEventLog(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    external_event_id="msg:delivery-triage",
                    event_type="text",
                    payload_digest="d" * 64,
                    received_at=datetime.now(UTC),
                    status="processed",
                )
            )
            db.add(
                EmployeeRequest(
                    id=request_id,
                    tenant_id=tenant_id,
                    created_by=employee_id,
                    request_type="other",
                    title="Connector request",
                    description="employee question",
                    status="submitted",
                    hr_owner_id=hr_id,
                    connector_source_id=source_id,
                    connector_external_event_id="msg:delivery-triage",
                    external_sender_id="wecom-employee-a",
                )
            )
            await db.commit()

        result = await hr_triage(
            tenant_id,
            hr_id,
            "hrbp",
            request_id,
            HrTriageBody(
                status="in_progress",
                next_step_for_employee="明天上午前回复",
                hr_note="内部排班备注",
            ),
        )

        assert result["delivery"]["status"] == "simulated_accepted"
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            attempts = (
                await db.execute(
                    select(ConnectorDeliveryAttempt).where(
                        ConnectorDeliveryAttempt.tenant_id == tenant_id,
                        ConnectorDeliveryAttempt.employee_request_id == request_id,
                    )
                )
            ).scalars().all()
        assert len(attempts) == 1
        assert attempts[0].message_content == "明天上午前回复"
        assert "内部排班备注" not in attempts[0].message_content
        assert attempts[0].status == "simulated_accepted"

        intercorp_request_id = str(uuid4())
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                ConnectorEventLog(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    external_event_id="msg:delivery-intercorp",
                    event_type="text",
                    payload_digest="i" * 64,
                    received_at=datetime.now(UTC),
                    status="processed",
                )
            )
            db.add(
                EmployeeRequest(
                    id=intercorp_request_id,
                    tenant_id=tenant_id,
                    created_by=employee_id,
                    request_type="other",
                    title="Intercorp connector request",
                    description="employee question",
                    status="submitted",
                    hr_owner_id=hr_id,
                    connector_source_id=source_id,
                    connector_external_event_id="msg:delivery-intercorp",
                    external_sender_id="other-corp/employee",
                )
            )
            await db.commit()
        rejected = await hr_triage(
            tenant_id,
            hr_id,
            "hrbp",
            intercorp_request_id,
            HrTriageBody(status="in_progress", next_step_for_employee="请在 HR 系统查看"),
        )
        assert rejected["delivery"]["status"] == "rejected"
        assert rejected["delivery"]["retryable"] is False
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(delete(ConnectorDeliveryAttempt).where(ConnectorDeliveryAttempt.tenant_id == tenant_id))
            await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
            await db.execute(delete(ConnectorEventLog).where(ConnectorEventLog.tenant_id == tenant_id))
            await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_retryable_delivery_can_be_retried_by_visible_hr_only() -> None:
    _require_db_security_tests()
    from datetime import UTC, datetime

    from app.connectors.wecom_outbound import WeComOutboundSimulator
    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt, ConnectorEventLog
    from app.data.models.data_source import DataSource
    from app.data.models.infra import AuditLog
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.scenarios.employee_request.service import HrTriageBody, hr_triage, retry_hr_delivery
    from app.shared.errors import NotFoundError

    tenant_id = str(uuid4())
    hr_id, employee_id, source_id, request_id = [str(uuid4()) for _ in range(4)]
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add_all(
                [
                    User(id=hr_id, tenant_id=tenant_id, name="HR", email=f"{hr_id}@delivery.invalid", hashed_password="x", role="hrbp"),
                    User(id=employee_id, tenant_id=tenant_id, name="Employee", email=f"{employee_id}@delivery.invalid", hashed_password="x", role="employee"),
                    DataSource(id=source_id, tenant_id=tenant_id, name="WeCom intake", platform="wecom", purpose="employee request", authorized_scope="direct messages", content_types="[]", data_destination="employee requests", event_route="employee_request", created_by=hr_id, wecom_callback_config_encrypted=b"configured-local-simulator"),
                ]
            )
            await db.flush()
            db.add(ConnectorEventLog(tenant_id=tenant_id, source_id=source_id, external_event_id="msg:delivery-retry", event_type="text", payload_digest="r" * 64, received_at=datetime.now(UTC), status="processed"))
            db.add(EmployeeRequest(id=request_id, tenant_id=tenant_id, created_by=employee_id, request_type="other", title="Connector request", description="employee question", status="submitted", hr_owner_id=hr_id, connector_source_id=source_id, connector_external_event_id="msg:delivery-retry", external_sender_id="wecom-employee-a"))
            await db.commit()

        failed = await hr_triage(
            tenant_id,
            hr_id,
            "hrbp",
            request_id,
            HrTriageBody(status="in_progress", next_step_for_employee="稍后回复"),
            gateway=WeComOutboundSimulator(fault_mode="timeout"),
        )
        assert failed["delivery"]["status"] == "retryable_failed"

        retried = await retry_hr_delivery(
            tenant_id, hr_id, "hrbp", request_id, failed["delivery"]["attempt_id"]
        )
        assert retried.status == "simulated_accepted"
        assert retried.attempt_count == 2

        with pytest.raises(NotFoundError):
            await retry_hr_delivery(
                str(uuid4()), str(uuid4()), "hr_manager", request_id, failed["delivery"]["attempt_id"]
            )
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(delete(ConnectorDeliveryAttempt).where(ConnectorDeliveryAttempt.tenant_id == tenant_id))
            await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
            await db.execute(delete(ConnectorEventLog).where(ConnectorEventLog.tenant_id == tenant_id))
            await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_concurrent_same_triage_version_creates_one_delivery_attempt() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for isolated PostgreSQL delivery concurrency verification")
    import asyncio
    from datetime import UTC, datetime

    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt, ConnectorEventLog
    from app.data.models.data_source import DataSource
    from app.data.models.infra import AuditLog
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.scenarios.employee_request.service import HrTriageBody, hr_triage

    tenant_id = str(uuid4())
    hr_id, employee_id, source_id, request_id = [str(uuid4()) for _ in range(4)]
    factory = get_session_factory()
    try:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add_all([
                User(id=hr_id, tenant_id=tenant_id, name="HR", email=f"{hr_id}@delivery.invalid", hashed_password="x", role="hrbp"),
                User(id=employee_id, tenant_id=tenant_id, name="Employee", email=f"{employee_id}@delivery.invalid", hashed_password="x", role="employee"),
                DataSource(id=source_id, tenant_id=tenant_id, name="WeCom intake", platform="wecom", purpose="employee request", authorized_scope="direct messages", content_types="[]", data_destination="employee requests", event_route="employee_request", created_by=hr_id, wecom_callback_config_encrypted=b"configured-local-simulator"),
            ])
            await db.flush()
            db.add(ConnectorEventLog(tenant_id=tenant_id, source_id=source_id, external_event_id="msg:delivery-concurrent", event_type="text", payload_digest="c" * 64, received_at=datetime.now(UTC), status="processed"))
            db.add(EmployeeRequest(id=request_id, tenant_id=tenant_id, created_by=employee_id, request_type="other", title="Connector request", description="employee question", status="submitted", hr_owner_id=hr_id, connector_source_id=source_id, connector_external_event_id="msg:delivery-concurrent", external_sender_id="wecom-employee-a"))
            await db.commit()

        tasks = [
            asyncio.create_task(
                hr_triage(tenant_id, hr_id, "hrbp", request_id, HrTriageBody(status="in_progress", next_step_for_employee="同一处理版本"))
            )
            for _ in range(8)
        ]
        done, pending = await asyncio.wait(tasks, timeout=10)
        if pending:
            stacks = []
            for task in pending:
                frame = task.get_stack(limit=1)
                stacks.append(frame[-1].f_code.co_name if frame else "no-frame")
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            pytest.fail(f"concurrent triage did not settle within 10 seconds: {stacks}")
        results = [task.result() for task in done]
        assert all(item["request"]["status"] == "in_progress" for item in results)

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            attempts = (
                await db.execute(select(ConnectorDeliveryAttempt).where(ConnectorDeliveryAttempt.tenant_id == tenant_id))
            ).scalars().all()
        assert len(attempts) == 1
        assert attempts[0].status == "simulated_accepted"
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            await db.execute(delete(ConnectorDeliveryAttempt).where(ConnectorDeliveryAttempt.tenant_id == tenant_id))
            await db.execute(delete(EmployeeRequest).where(EmployeeRequest.tenant_id == tenant_id))
            await db.execute(delete(ConnectorEventLog).where(ConnectorEventLog.tenant_id == tenant_id))
            await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()
