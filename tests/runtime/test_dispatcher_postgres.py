"""Real PostgreSQL coverage for the governed tool dispatcher."""

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from starlette.requests import Request

from app.access.routes.notifications import list_notifications, mark_notification_read
from app.data.database import make_tenant_session
from app.data.models.hr_case import AgentRun, ApprovalRequest, CaseEvent, CasePlan, HRCase, ToolExecution
from app.data.models.notification import InAppNotification
from app.data.models.runtime import ExecutionGrant, OutboxMessage
from app.data.models.user import User
from app.data.models.work_task import WorkTask
from app.outbox.dispatcher import (
    OutboxDispatcher,
    RetryableToolDispatchError,
    TerminalToolDispatchError,
    ToolInvocation,
    UnknownToolOutcomeError,
)
from app.outbox.worker import drain_tool_dispatches
from app.scenarios.hr_case_agent.service import HRCaseService
from app.shared.errors import NotFoundError
from app.tools.gateway import ToolGateway


@dataclass(frozen=True)
class PreparedDispatch:
    tenant_id: str
    user_id: str
    case_id: str
    execution_id: str
    grant_id: str
    outbox_id: str


def _notification_request(tenant_id: str, user_id: str, role: str = "hrbp") -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/api/notifications", "headers": []})
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    request.state.user_role = role
    return request


async def _prepare_dispatch(
    request_id: str = "dispatcher-request-1",
    *,
    tool_name: str = "create_hr_case",
    params: dict | None = None,
    user_id: str | None = None,
) -> PreparedDispatch:
    tenant_id, user_id = str(uuid4()), user_id or str(uuid4())
    request_params = params or {
        "title": "Dispatch target",
        "subject_ref": "EMP-DISPATCH-001",
        "category": "overtime",
    }
    # A self-recipient is only known after this helper creates the isolated
    # actor.  Keep the substitution local to the test fixture so production
    # payloads always contain the final user ID before approval.
    if request_params.get("recipient_ref") == "$actor":
        request_params = {**request_params, "recipient_ref": user_id}
    session = await make_tenant_session(tenant_id)
    try:
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="Dispatcher HRBP",
                email=f"dispatcher-{tenant_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hrbp",
            )
        )
        await session.flush()
        service = HRCaseService(
            session,
            tenant_id,
            actor=f"user:{user_id}|role:hrbp",
            visible_user_ids={user_id},
        )
        case = await service.create_case(user_id, "EMP-DISPATCH-001", "overtime", "Dispatcher verification")
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        plan = await service.save_plan(case.id, steps=[])
        approval = await service.request_approval(
            case.id,
            tool_name,
            request_params,
            plan_id=plan.id,
        )
        await service.decide_approval(case.id, approval.id, user_id, "approve", "verified", role="hr_manager")
        prepared = await ToolGateway(service).prepare_approved_write(case.id, approval.id, request_id)
        await session.commit()
        return PreparedDispatch(
            tenant_id=tenant_id,
            user_id=user_id,
            case_id=case.id,
            execution_id=prepared.execution_id,
            grant_id=prepared.grant_id,
            outbox_id=prepared.outbox_id,
        )
    finally:
        await session.close()


async def _cleanup_dispatch(prepared: PreparedDispatch) -> None:
    session = await make_tenant_session(prepared.tenant_id)
    try:
        await session.execute(delete(InAppNotification).where(InAppNotification.tenant_id == prepared.tenant_id))
        await session.execute(delete(WorkTask).where(WorkTask.tenant_id == prepared.tenant_id))
        await session.execute(delete(ToolExecution).where(ToolExecution.tenant_id == prepared.tenant_id))
        await session.execute(delete(OutboxMessage).where(OutboxMessage.tenant_id == prepared.tenant_id))
        await session.execute(delete(ExecutionGrant).where(ExecutionGrant.tenant_id == prepared.tenant_id))
        await session.execute(delete(CaseEvent).where(CaseEvent.tenant_id == prepared.tenant_id))
        await session.execute(delete(ApprovalRequest).where(ApprovalRequest.tenant_id == prepared.tenant_id))
        await session.execute(delete(CasePlan).where(CasePlan.tenant_id == prepared.tenant_id))
        await session.execute(delete(AgentRun).where(AgentRun.tenant_id == prepared.tenant_id))
        await session.execute(delete(HRCase).where(HRCase.tenant_id == prepared.tenant_id))
        await session.execute(delete(User).where(User.tenant_id == prepared.tenant_id))
        await session.commit()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_dispatcher_consumes_grant_calls_tool_and_completes_with_fence() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch()
    calls: list[ToolInvocation] = []

    async def execute(invocation: ToolInvocation) -> dict:
        calls.append(invocation)
        return {"summary": "downstream accepted"}

    try:
        result = await OutboxDispatcher(
            {"create_hr_case": execute},
            worker_id="dispatcher-worker-1",
            lease_seconds=30,
        ).dispatch_once(prepared.tenant_id)

        assert result.state == "succeeded"
        assert result.execution_id == prepared.execution_id
        assert len(calls) == 1
        assert calls[0].tenant_id == prepared.tenant_id
        assert calls[0].execution_id == prepared.execution_id
        assert calls[0].idempotency_key == "dispatcher-request-1"
        assert calls[0].params == {
            "title": "Dispatch target",
            "subject_ref": "EMP-DISPATCH-001",
            "category": "overtime",
            "risk_level": "LOW",
        }

        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None
            assert execution.status == "SUCCEEDED"
            assert execution.result_summary == "downstream accepted"
            assert execution.dispatch_lease_owner is None
            assert execution.dispatch_lease_token == 1
            assert message is not None and message.status == "delivered"
            assert message.lease_owner is None
            assert grant is not None and grant.status == "consumed"
            assert grant.consumption_count == 1
            assert case is not None and case.status == "RESOLVED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_worker_delivers_one_recipient_scoped_in_app_notification() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch(
        "dispatcher-notification-1",
        tool_name="send_case_notification",
        params={
            "channel": "in_app",
            "recipient_ref": "$actor",
            "template": "case_owner_action_required",
        },
    )
    try:
        states = await drain_tool_dispatches(
            prepared.tenant_id,
            max_messages=10,
            worker_id="dispatcher-notification-worker",
        )
        assert states == ["succeeded"]
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            notifications = (
                await verify.scalars(
                    select(InAppNotification).where(
                        InAppNotification.tenant_id == prepared.tenant_id,
                        InAppNotification.tool_execution_id == prepared.execution_id,
                    )
                )
            ).all()
            assert len(notifications) == 1
            delivery = notifications[0]
            assert delivery.recipient_user_id == prepared.user_id
            assert delivery.case_id == prepared.case_id
            assert delivery.template == "case_owner_action_required"
            assert delivery.delivery_key == f"in-app-notification:{prepared.execution_id}"

            recipient_request = _notification_request(prepared.tenant_id, prepared.user_id)
            inbox = await list_notifications(request=recipient_request, limit=50, session=verify)
            assert len(inbox["notifications"]) == 1
            assert inbox["notifications"][0]["notification_id"] == delivery.id
            assert inbox["notifications"][0]["read_at"] is None
            assert "params" not in inbox["notifications"][0]
            acknowledged = await mark_notification_read(delivery.id, request=recipient_request, session=verify)
            assert acknowledged["read_at"] is not None

            outsider_request = _notification_request(prepared.tenant_id, str(uuid4()), role="employee")
            assert await list_notifications(request=outsider_request, limit=50, session=verify) == {"notifications": []}
            with pytest.raises(NotFoundError):
                await mark_notification_read(delivery.id, request=outsider_request, session=verify)
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_rechecks_current_permission_before_external_call() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-revoked-1")
    calls: list[ToolInvocation] = []

    async def must_not_execute(invocation: ToolInvocation) -> dict:
        calls.append(invocation)
        return {"summary": "must not happen"}

    try:
        revoke = await make_tenant_session(prepared.tenant_id)
        try:
            user = await revoke.scalar(select(User).where(User.id == prepared.user_id))
            assert user is not None
            user.role = "employee"
            await revoke.commit()
        finally:
            await revoke.close()

        result = await OutboxDispatcher(
            {"create_hr_case": must_not_execute},
            worker_id="dispatcher-worker-revoked",
        ).dispatch_once(prepared.tenant_id)

        assert result.state == "authorization_revoked"
        assert calls == []
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None
            assert execution.status == "FAILED"
            assert execution.error_code == "AUTHORIZATION_REVOKED"
            assert message is not None and message.status == "terminal"
            assert grant is not None and grant.status == "revoked"
            assert grant.consumption_count == 0
            assert case is not None and case.status == "FAILED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_records_unknown_without_blind_retry() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-unknown-1")
    calls = 0

    async def response_lost(_invocation: ToolInvocation) -> dict:
        nonlocal calls
        calls += 1
        raise UnknownToolOutcomeError("DOWNSTREAM_RESPONSE_LOST", "request may have completed")

    try:
        result = await OutboxDispatcher(
            {"create_hr_case": response_lost},
            worker_id="dispatcher-worker-unknown",
        ).dispatch_once(prepared.tenant_id)

        assert result.state == "unknown"
        assert calls == 1
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None
            assert execution.status == "UNKNOWN"
            assert execution.error_code == "DOWNSTREAM_RESPONSE_LOST"
            assert execution.dispatch_lease_owner is None
            assert message is not None and message.status == "terminal"
            assert message.attempt_count == 1
            assert grant is not None and grant.status == "consumed"
            assert grant.consumption_count == 1
            assert case is not None and case.status == "EXECUTING"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_retries_with_backoff_then_moves_to_dead_letter() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-retry-1")
    calls = 0
    started_at = datetime.now(UTC)

    async def unavailable_before_send(_invocation: ToolInvocation) -> dict:
        nonlocal calls
        calls += 1
        raise RetryableToolDispatchError("DOWNSTREAM_UNAVAILABLE", "connection was not established")

    dispatcher = OutboxDispatcher(
        {"create_hr_case": unavailable_before_send},
        worker_id="dispatcher-worker-retry",
        lease_seconds=30,
        retry_base_seconds=10,
        jitter_source=lambda: 0.0,
    )
    try:
        first = await dispatcher.dispatch_once(prepared.tenant_id, now=started_at)
        assert first.state == "retry_scheduled"
        verify_first = await make_tenant_session(prepared.tenant_id)
        try:
            message = await verify_first.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            execution = await verify_first.scalar(
                select(ToolExecution).where(ToolExecution.id == prepared.execution_id)
            )
            assert message is not None and message.status == "pending"
            assert message.attempt_count == 1
            assert message.next_attempt_at == started_at + timedelta(seconds=10)
            assert execution is not None and execution.status == "PENDING"
        finally:
            await verify_first.close()

        second_at = started_at + timedelta(seconds=10)
        second = await dispatcher.dispatch_once(prepared.tenant_id, now=second_at)
        assert second.state == "retry_scheduled"
        verify_second = await make_tenant_session(prepared.tenant_id)
        try:
            message = await verify_second.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            assert message is not None and message.status == "pending"
            assert message.attempt_count == 2
            assert message.next_attempt_at == second_at + timedelta(seconds=20)
        finally:
            await verify_second.close()

        third_at = second_at + timedelta(seconds=20)
        third = await dispatcher.dispatch_once(prepared.tenant_id, now=third_at)
        assert third.state == "dead_letter"
        assert calls == 3

        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None and execution.status == "FAILED"
            assert execution.error_code == "DOWNSTREAM_UNAVAILABLE"
            assert execution.attempt == 3
            assert execution.dispatch_lease_owner is None
            assert message is not None and message.status == "dead_letter"
            assert message.attempt_count == 3
            assert grant is not None and grant.consumption_count == 1
            assert case is not None and case.status == "FAILED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_records_deterministic_failure_without_retry() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-terminal-1")
    calls = 0

    async def rejected(_invocation: ToolInvocation) -> dict:
        nonlocal calls
        calls += 1
        raise TerminalToolDispatchError("DOWNSTREAM_REJECTED", "request was explicitly rejected")

    try:
        result = await OutboxDispatcher(
            {"create_hr_case": rejected},
            worker_id="dispatcher-worker-terminal",
        ).dispatch_once(prepared.tenant_id)

        assert result.state == "failed"
        assert calls == 1
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None and execution.status == "FAILED"
            assert execution.error_code == "DOWNSTREAM_REJECTED"
            assert execution.attempt == 1
            assert message is not None and message.status == "terminal"
            assert message.attempt_count == 1
            assert case is not None and case.status == "FAILED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_expired_worker_is_fenced_after_another_worker_completes() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-fence-1")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    idempotency_keys: list[str] = []
    started_at = datetime.now(UTC)

    async def slow_first(invocation: ToolInvocation) -> dict:
        idempotency_keys.append(invocation.idempotency_key)
        first_started.set()
        await release_first.wait()
        return {"summary": "stale worker result"}

    async def replacement(invocation: ToolInvocation) -> dict:
        idempotency_keys.append(invocation.idempotency_key)
        return {"summary": "replacement worker result"}

    first_dispatcher = OutboxDispatcher(
        {"create_hr_case": slow_first},
        worker_id="dispatcher-worker-stale",
        lease_seconds=1,
    )
    second_dispatcher = OutboxDispatcher(
        {"create_hr_case": replacement},
        worker_id="dispatcher-worker-replacement",
        lease_seconds=30,
    )
    first_task: asyncio.Task | None = None
    try:
        first_task = asyncio.create_task(first_dispatcher.dispatch_once(prepared.tenant_id, now=started_at))
        await asyncio.wait_for(first_started.wait(), timeout=5)

        second = await second_dispatcher.dispatch_once(
            prepared.tenant_id,
            now=started_at + timedelta(seconds=2),
        )
        assert second.state == "succeeded"
        release_first.set()
        first = await first_task
        assert first.state == "stale"
        assert idempotency_keys == ["dispatcher-fence-1", "dispatcher-fence-1"]

        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            assert execution is not None and execution.status == "SUCCEEDED"
            assert execution.result_summary == "replacement worker result"
            assert execution.dispatch_lease_token == 2
            assert execution.attempt == 2
            assert message is not None and message.status == "delivered"
            assert message.lease_token == 2
            assert grant is not None and grant.consumption_count == 1
        finally:
            await verify.close()
    finally:
        release_first.set()
        if first_task is not None and not first_task.done():
            await first_task
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_rejects_an_expired_unconsumed_grant() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-expired-grant-1")
    calls = 0
    dispatch_at = datetime.now(UTC)

    async def must_not_execute(_invocation: ToolInvocation) -> dict:
        nonlocal calls
        calls += 1
        return {"summary": "must not happen"}

    try:
        expire = await make_tenant_session(prepared.tenant_id)
        try:
            grant = await expire.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            assert grant is not None
            grant.expires_at = dispatch_at - timedelta(seconds=1)
            await expire.commit()
        finally:
            await expire.close()

        result = await OutboxDispatcher(
            {"create_hr_case": must_not_execute},
            worker_id="dispatcher-worker-expired-grant",
        ).dispatch_once(prepared.tenant_id, now=dispatch_at)

        assert result.state == "authorization_revoked"
        assert calls == 0
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            grant = await verify.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            assert execution is not None and execution.error_code == "AUTHORIZATION_REVOKED"
            assert message is not None and message.status == "terminal"
            assert grant is not None and grant.status == "revoked"
            assert grant.consumption_count == 0
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_dead_letters_a_missing_executor_without_leaking_lease() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-missing-executor-1")
    try:
        result = await OutboxDispatcher(
            {},
            worker_id="dispatcher-worker-missing-executor",
        ).dispatch_once(prepared.tenant_id)

        assert result.state == "dead_letter"
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert execution is not None and execution.status == "FAILED"
            assert execution.error_code == "EXECUTOR_NOT_REGISTERED"
            assert execution.dispatch_lease_owner is None
            assert message is not None and message.status == "dead_letter"
            assert message.lease_owner is None
            assert case is not None and case.status == "FAILED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dispatcher_leaves_other_outbox_event_types_for_their_own_consumer() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-filtered-claim-1")
    foreign_outbox_id: str | None = None

    async def execute(_invocation: ToolInvocation) -> dict:
        return {"summary": "governed call completed"}

    try:
        seed = await make_tenant_session(prepared.tenant_id)
        try:
            foreign = OutboxMessage(
                tenant_id=prepared.tenant_id,
                event_type="connector.sync",
                schema_version=1,
                queue_name="connector-sync",
                aggregate_type="connector",
                aggregate_id="connector-foreign-1",
                dedupe_key="connector-foreign-1",
                payload_digest="f" * 64,
                next_attempt_at=datetime.now(UTC),
            )
            seed.add(foreign)
            await seed.commit()
            foreign_outbox_id = foreign.id
        finally:
            await seed.close()

        dispatcher = OutboxDispatcher({"create_hr_case": execute}, worker_id="dispatcher-worker-filtered")
        assert (await dispatcher.dispatch_once(prepared.tenant_id)).state == "succeeded"
        assert (await dispatcher.dispatch_once(prepared.tenant_id)).state == "idle"

        verify = await make_tenant_session(prepared.tenant_id)
        try:
            foreign = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == foreign_outbox_id))
            assert foreign is not None
            assert foreign.status == "pending"
            assert foreign.attempt_count == 0
            assert foreign.lease_owner is None
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_governed_create_work_task_executor_closes_the_real_db_loop() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch(
        "dispatcher-work-task-1",
        tool_name="create_work_task",
        params={
            "title": "Follow up overtime evidence",
            "next_action": "Collect signed attendance record",
            "waiting_for": "employee",
        },
    )
    try:
        states = await drain_tool_dispatches(
            prepared.tenant_id,
            max_messages=10,
            worker_id="dispatcher-worker-work-task",
        )

        assert states == ["succeeded"]
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            tasks = (
                (
                    await verify.execute(
                        select(WorkTask).where(
                            WorkTask.tenant_id == prepared.tenant_id,
                            WorkTask.idempotency_key == "dispatcher-work-task-1",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(tasks) == 1
            assert tasks[0].created_by == prepared.user_id
            assert tasks[0].owner_user_id == prepared.user_id
            assert tasks[0].title == "Follow up overtime evidence"
            assert tasks[0].next_action == "Collect signed attendance record"
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            assert execution is not None and execution.status == "SUCCEEDED"
            assert tasks[0].id in (execution.result_summary or "")
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_governed_create_hr_case_executor_uses_a_stable_request_identity() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch(
        "dispatcher-create-case-1",
        tool_name="create_hr_case",
        params={
            "title": "New governed case",
            "subject_ref": "EMP-NEW-001",
            "category": "attendance",
            "description": "created by the governed dispatcher",
        },
    )
    try:
        assert await drain_tool_dispatches(prepared.tenant_id, max_messages=10) == ["succeeded"]
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            created = (
                (
                    await verify.execute(
                        select(HRCase).where(
                            HRCase.tenant_id == prepared.tenant_id,
                            HRCase.subject_ref == "EMP-NEW-001",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(created) == 1
            assert created[0].created_by == prepared.user_id
            assert created[0].title == "New governed case"
            assert created[0].description == "created by the governed dispatcher"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_governed_assign_case_owner_executor_mutates_only_the_approved_case() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    owner_id = str(uuid4())
    prepared = await _prepare_dispatch(
        "dispatcher-assign-owner-1",
        tool_name="assign_case_owner",
        params={"owner_id": owner_id},
        user_id=owner_id,
    )
    try:
        assert await drain_tool_dispatches(prepared.tenant_id, max_messages=10) == ["succeeded"]
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            assert case is not None and case.owner_id == prepared.user_id
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_governed_update_case_status_executor_resolves_the_approved_case() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch(
        "dispatcher-resolve-case-1",
        tool_name="update_case_status",
        params={"status": "RESOLVED"},
    )
    try:
        assert await drain_tool_dispatches(prepared.tenant_id, max_messages=10) == ["succeeded"]
        verify = await make_tenant_session(prepared.tenant_id)
        try:
            case = await verify.scalar(select(HRCase).where(HRCase.id == prepared.case_id))
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            assert case is not None and case.status == "RESOLVED"
            assert execution is not None and execution.status == "SUCCEEDED"
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)


@pytest.mark.asyncio
async def test_dead_letter_replay_reauthorizes_before_requeue() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL dispatcher verification")

    prepared = await _prepare_dispatch("dispatcher-replay-1")
    replay_at = datetime.now(UTC)
    calls = 0

    async def execute(_invocation: ToolInvocation) -> dict:
        nonlocal calls
        calls += 1
        return {"summary": "replayed successfully"}

    try:
        dead_letter = await OutboxDispatcher(
            {},
            worker_id="dispatcher-worker-dlq-create",
        ).dispatch_once(prepared.tenant_id, now=replay_at)
        assert dead_letter.state == "dead_letter"

        role_change = await make_tenant_session(prepared.tenant_id)
        try:
            user = await role_change.scalar(select(User).where(User.id == prepared.user_id))
            assert user is not None
            user.role = "employee"
            await role_change.commit()
        finally:
            await role_change.close()

        dispatcher = OutboxDispatcher(
            {"create_hr_case": execute},
            worker_id="dispatcher-worker-dlq-replay",
        )
        denied = await dispatcher.replay_dead_letter(
            prepared.tenant_id,
            prepared.outbox_id,
            now=replay_at + timedelta(seconds=1),
        )
        assert denied is False
        assert calls == 0

        role_restore = await make_tenant_session(prepared.tenant_id)
        try:
            user = await role_restore.scalar(select(User).where(User.id == prepared.user_id))
            assert user is not None
            user.role = "hrbp"
            grant = await role_restore.scalar(select(ExecutionGrant).where(ExecutionGrant.id == prepared.grant_id))
            assert grant is not None
            grant.status = "consumed"
            await role_restore.commit()
        finally:
            await role_restore.close()

        replayed = await dispatcher.replay_dead_letter(
            prepared.tenant_id,
            prepared.outbox_id,
            now=replay_at + timedelta(seconds=2),
        )
        assert replayed is True
        completed = await dispatcher.dispatch_once(
            prepared.tenant_id,
            now=replay_at + timedelta(seconds=2),
        )
        assert completed.state == "succeeded"
        assert calls == 1

        verify = await make_tenant_session(prepared.tenant_id)
        try:
            execution = await verify.scalar(select(ToolExecution).where(ToolExecution.id == prepared.execution_id))
            message = await verify.scalar(select(OutboxMessage).where(OutboxMessage.id == prepared.outbox_id))
            assert execution is not None and execution.status == "SUCCEEDED"
            assert execution.result_summary == "replayed successfully"
            assert message is not None and message.status == "delivered"
            assert message.attempt_count == 1
            assert message.lease_token == 2
        finally:
            await verify.close()
    finally:
        await _cleanup_dispatch(prepared)
