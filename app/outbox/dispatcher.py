"""Governed ToolExecution dispatcher with durable leases and fencing.

Transaction and failure windows::

    tx 1: claim Outbox -> re-authorize -> consume Grant -> fence Execution -> COMMIT
                                      |
                                      v
                      external tool call (no DB transaction)
                                      |
                                      v
    tx 2: lock Outbox -> verify fence -> complete Execution/Event/Case -> COMMIT

    worker A token=1 --lease expires--> worker B token=2
           late completion(token=1) --X--> no state write

The outbox may redeliver.  A stable request id is therefore passed to the
downstream as its idempotency key, and stale workers are fenced at completion.
"""

from __future__ import annotations

import json
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.approvals.grants import ExecutionGrantRepository
from app.data.database import make_tenant_session
from app.data.models.access_scope import ManagerOrgScope
from app.data.models.hr_case import ApprovalRequest, HRCase, ToolExecution
from app.data.models.runtime import ExecutionGrant, OutboxMessage
from app.data.models.user import User
from app.outbox.repository import OutboxRepository
from app.scenarios.hr_case_agent import state as case_state
from app.scenarios.hr_case_agent.service import HRCaseService
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call


@dataclass(frozen=True)
class ToolInvocation:
    tenant_id: str
    execution_id: str
    case_id: str
    tool_name: str
    params: dict
    idempotency_key: str
    subject_id: str
    subject_role: str


ToolExecutor = Callable[[ToolInvocation], Awaitable[dict]]
TenantSessionFactory = Callable[[str], Awaitable[AsyncSession]]


class UnknownToolOutcomeError(Exception):
    """The request may have reached the downstream; blind retry is unsafe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RetryableToolDispatchError(Exception):
    """The call is known not to have produced a side effect and may retry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TerminalToolDispatchError(Exception):
    """The downstream definitively rejected the action; retry is incorrect."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DispatchResult:
    state: str
    execution_id: str | None = None
    outbox_id: str | None = None


@dataclass(frozen=True)
class _ClaimedDispatch:
    invocation: ToolInvocation
    outbox_id: str
    lease_token: int


class OutboxDispatcher:
    """Claim and execute one tenant-scoped governed tool message."""

    def __init__(
        self,
        executors: Mapping[str, ToolExecutor],
        *,
        worker_id: str,
        lease_seconds: int = 30,
        retry_base_seconds: int = 5,
        jitter_source: Callable[[], float] = random.random,
        session_factory: TenantSessionFactory = make_tenant_session,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        self._executors = dict(executors)
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._jitter_source = jitter_source
        self._session_factory = session_factory

    async def dispatch_once(self, tenant_id: str, *, now: datetime | None = None) -> DispatchResult:
        claimed_at = now or datetime.now(UTC)
        claimed = await self._claim(tenant_id, claimed_at)
        if claimed is None:
            return DispatchResult(state="idle")
        if isinstance(claimed, DispatchResult):
            return claimed

        executor = self._executors.get(claimed.invocation.tool_name)
        if executor is None:
            completed = await self._complete_failed(
                tenant_id,
                claimed,
                "EXECUTOR_NOT_REGISTERED",
                f"no governed executor registered for {claimed.invocation.tool_name}",
                dead_letter=True,
            )
            return DispatchResult(
                state="dead_letter" if completed else "stale",
                execution_id=claimed.invocation.execution_id,
                outbox_id=claimed.outbox_id,
            )

        try:
            outcome = await executor(claimed.invocation)
        except RetryableToolDispatchError as error:
            state = await self._complete_retryable(tenant_id, claimed, error, claimed_at)
            return DispatchResult(
                state=state,
                execution_id=claimed.invocation.execution_id,
                outbox_id=claimed.outbox_id,
            )
        except TerminalToolDispatchError as error:
            completed = await self._complete_failed(tenant_id, claimed, error.code, str(error))
            return DispatchResult(
                state="failed" if completed else "stale",
                execution_id=claimed.invocation.execution_id,
                outbox_id=claimed.outbox_id,
            )
        except UnknownToolOutcomeError as error:
            completed = await self._complete_unknown(tenant_id, claimed, error.code, str(error))
            return DispatchResult(
                state="unknown" if completed else "stale",
                execution_id=claimed.invocation.execution_id,
                outbox_id=claimed.outbox_id,
            )
        except Exception as error:
            completed = await self._complete_unknown(
                tenant_id,
                claimed,
                "UNCLASSIFIED_TOOL_EXCEPTION",
                str(error),
            )
            return DispatchResult(
                state="unknown" if completed else "stale",
                execution_id=claimed.invocation.execution_id,
                outbox_id=claimed.outbox_id,
            )
        summary = str(outcome.get("summary", ""))[:500]
        completed = await self._complete_success(tenant_id, claimed, summary)
        return DispatchResult(
            state="succeeded" if completed else "stale",
            execution_id=claimed.invocation.execution_id,
            outbox_id=claimed.outbox_id,
        )

    async def replay_dead_letter(
        self,
        tenant_id: str,
        outbox_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Requeue one DLQ item only after current authorization succeeds."""
        replayed_at = now or datetime.now(UTC)
        session = await self._session_factory(tenant_id)
        try:
            message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.id == outbox_id,
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.status == "dead_letter",
                    OutboxMessage.event_type == "tool.dispatch",
                    OutboxMessage.aggregate_type == "tool_execution",
                )
                .with_for_update()
            )
            if message is None:
                await session.rollback()
                return False
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == message.aggregate_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.outbox_message_id == message.id,
                    ToolExecution.status == "FAILED",
                )
                .with_for_update()
            )
            if execution is None or execution.execution_grant_id is None:
                await session.rollback()
                return False
            grant = await session.scalar(
                select(ExecutionGrant).where(
                    ExecutionGrant.id == execution.execution_grant_id,
                    ExecutionGrant.tenant_id == tenant_id,
                    ExecutionGrant.status == "consumed",
                    ExecutionGrant.consumption_count == 1,
                )
            )
            if grant is None or not await self._is_currently_authorized(
                session,
                tenant_id,
                execution,
                grant,
                replayed_at,
            ):
                await session.rollback()
                return False

            message.status = "pending"
            message.attempt_count = 0
            message.next_attempt_at = replayed_at
            message.lease_owner = None
            message.lease_expires_at = None
            message.last_error_code = None
            execution.status = "PENDING"
            execution.error_code = None
            execution.error_message = None
            execution.dispatch_lease_owner = None
            execution.dispatch_lease_expires_at = None
            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            case = await service.get_case(execution.case_id)
            if case.status != case_state.FAILED:
                await session.rollback()
                return False
            await service.transition_case(execution.case_id, case_state.EXECUTING, reason="DLQ replay authorized")
            await service._append_event(
                execution.case_id,
                "TOOL_EXECUTION_REPLAYED",
                {"tool": execution.tool_name, "outbox_id": message.id},
                agent_run_id=execution.agent_run_id,
            )
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _claim(self, tenant_id: str, now: datetime) -> _ClaimedDispatch | DispatchResult | None:
        session = await self._session_factory(tenant_id)
        try:
            message = await OutboxRepository(session, tenant_id).claim_next(
                self._worker_id,
                self._lease_seconds,
                now,
                event_type="tool.dispatch",
                aggregate_type="tool_execution",
            )
            if message is None:
                await session.rollback()
                return None
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == message.aggregate_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.outbox_message_id == message.id,
                )
                .with_for_update()
            )
            if execution is None or execution.execution_grant_id is None or execution.approval_id is None:
                raise RuntimeError("tool dispatch references are incomplete")

            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == execution.approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.case_id == execution.case_id,
                )
            )
            grant = await session.scalar(
                select(ExecutionGrant).where(
                    ExecutionGrant.id == execution.execution_grant_id,
                    ExecutionGrant.tenant_id == tenant_id,
                )
            )
            if approval is None or grant is None:
                raise RuntimeError("tool dispatch approval or grant is missing")

            params = validate_tool_call(execution.tool_name, json.loads(approval.params_json))
            if approval.input_hash != execution.input_hash or message.payload_digest != execution.input_hash:
                raise RuntimeError("tool dispatch payload digest does not match approval")
            if not await self._is_currently_authorized(session, tenant_id, execution, grant, now):
                grant.status = "revoked"
                execution.status = "FAILED"
                execution.error_code = "AUTHORIZATION_REVOKED"
                execution.error_message = "current capability or object access no longer permits execution"
                execution.attempt = message.attempt_count
                execution.dispatch_lease_owner = None
                execution.dispatch_lease_token = message.lease_token
                execution.dispatch_lease_expires_at = None
                service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
                case = await service.get_case(execution.case_id)
                if case.status == case_state.AWAITING_APPROVAL:
                    await service.transition_case(
                        execution.case_id, case_state.EXECUTING, reason="authorization recheck"
                    )
                if case.status == case_state.EXECUTING:
                    await service.transition_case(
                        execution.case_id,
                        case_state.FAILED,
                        reason="AUTHORIZATION_REVOKED",
                    )
                terminal = await OutboxRepository(session, tenant_id).mark_terminal(
                    message.id,
                    self._worker_id,
                    message.lease_token,
                    "AUTHORIZATION_REVOKED",
                )
                if not terminal:
                    await session.rollback()
                    return DispatchResult(
                        state="stale",
                        execution_id=execution.id,
                        outbox_id=message.id,
                    )
                await session.commit()
                return DispatchResult(
                    state="authorization_revoked",
                    execution_id=execution.id,
                    outbox_id=message.id,
                )

            if grant.status == "active":
                consumed = await ExecutionGrantRepository(session, tenant_id).consume(
                    grant.id,
                    allowed_tool=execution.tool_name,
                    allowed_action="execute",
                    object_type="hr_case",
                    object_id=execution.case_id,
                    normalized_params_hash=execution.input_hash,
                    now=now,
                )
                if consumed is None:
                    raise RuntimeError("tool dispatch grant cannot be consumed")
            elif not (
                grant.status == "consumed"
                and grant.consumption_count == 1
                and execution.status in {"PENDING", "RUNNING"}
                and execution.attempt >= 1
            ):
                raise RuntimeError("tool dispatch grant is not valid for this attempt")

            execution.status = "RUNNING"
            execution.attempt = message.attempt_count
            execution.dispatch_lease_owner = self._worker_id
            execution.dispatch_lease_token = message.lease_token
            execution.dispatch_lease_expires_at = message.lease_expires_at
            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            case = await service.get_case(execution.case_id)
            if case.status == case_state.AWAITING_APPROVAL:
                await service.transition_case(execution.case_id, case_state.EXECUTING, reason=execution.tool_name)
            user = await session.scalar(
                select(User).where(
                    User.id == grant.subject_id,
                    User.tenant_id == tenant_id,
                )
            )
            if user is None:
                raise RuntimeError("tool dispatch subject disappeared after authorization")
            await session.commit()
            return _ClaimedDispatch(
                invocation=ToolInvocation(
                    tenant_id=tenant_id,
                    execution_id=execution.id,
                    case_id=execution.case_id,
                    tool_name=execution.tool_name,
                    params=params,
                    idempotency_key=execution.request_id,
                    subject_id=grant.subject_id,
                    subject_role=user.role,
                ),
                outbox_id=message.id,
                lease_token=message.lease_token,
            )
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _complete_success(self, tenant_id: str, claimed: _ClaimedDispatch, summary: str) -> bool:
        session = await self._session_factory(tenant_id)
        try:
            message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.id == claimed.outbox_id,
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.status == "leased",
                    OutboxMessage.lease_owner == self._worker_id,
                    OutboxMessage.lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if message is None:
                await session.rollback()
                return False
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == claimed.invocation.execution_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.status == "RUNNING",
                    ToolExecution.dispatch_lease_owner == self._worker_id,
                    ToolExecution.dispatch_lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if execution is None:
                await session.rollback()
                return False
            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            await service.finish_tool_execution(execution.id, ok=True, result_summary=summary)
            execution.dispatch_lease_owner = None
            execution.dispatch_lease_expires_at = None
            case = await service.get_case(execution.case_id)
            if case.status == case_state.EXECUTING:
                await service.transition_case(
                    execution.case_id, case_state.RESOLVED, reason=f"{execution.tool_name} done"
                )
            elif case.status != case_state.RESOLVED:
                raise RuntimeError(f"tool {execution.tool_name} completed while case is unexpectedly {case.status}")
            delivered = await OutboxRepository(session, tenant_id).mark_delivered(
                message.id,
                self._worker_id,
                claimed.lease_token,
            )
            if not delivered:
                await session.rollback()
                return False
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _complete_unknown(
        self,
        tenant_id: str,
        claimed: _ClaimedDispatch,
        error_code: str,
        error_message: str,
    ) -> bool:
        session = await self._session_factory(tenant_id)
        try:
            message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.id == claimed.outbox_id,
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.status == "leased",
                    OutboxMessage.lease_owner == self._worker_id,
                    OutboxMessage.lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if message is None:
                await session.rollback()
                return False
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == claimed.invocation.execution_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.status == "RUNNING",
                    ToolExecution.dispatch_lease_owner == self._worker_id,
                    ToolExecution.dispatch_lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if execution is None:
                await session.rollback()
                return False
            execution.status = "UNKNOWN"
            execution.error_code = error_code[:50]
            execution.error_message = error_message[:2000]
            execution.dispatch_lease_owner = None
            execution.dispatch_lease_expires_at = None
            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            await service._append_event(
                execution.case_id,
                "TOOL_EXECUTION_UNKNOWN",
                {"tool": execution.tool_name, "error_code": execution.error_code},
                agent_run_id=execution.agent_run_id,
            )
            terminal = await OutboxRepository(session, tenant_id).mark_terminal(
                message.id,
                self._worker_id,
                claimed.lease_token,
                execution.error_code,
            )
            if not terminal:
                await session.rollback()
                return False
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _complete_failed(
        self,
        tenant_id: str,
        claimed: _ClaimedDispatch,
        error_code: str,
        error_message: str,
        *,
        dead_letter: bool = False,
    ) -> bool:
        session = await self._session_factory(tenant_id)
        try:
            message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.id == claimed.outbox_id,
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.status == "leased",
                    OutboxMessage.lease_owner == self._worker_id,
                    OutboxMessage.lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if message is None:
                await session.rollback()
                return False
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == claimed.invocation.execution_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.status == "RUNNING",
                    ToolExecution.dispatch_lease_owner == self._worker_id,
                    ToolExecution.dispatch_lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if execution is None:
                await session.rollback()
                return False
            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            await service.finish_tool_execution(
                execution.id,
                ok=False,
                error_code=error_code[:50],
                error_message=error_message[:2000],
            )
            execution.dispatch_lease_owner = None
            execution.dispatch_lease_expires_at = None
            await service.transition_case(execution.case_id, case_state.FAILED, reason=error_code[:50])
            terminal = await OutboxRepository(session, tenant_id).mark_terminal(
                message.id,
                self._worker_id,
                claimed.lease_token,
                error_code[:50],
                dead_letter=dead_letter,
            )
            if not terminal:
                await session.rollback()
                return False
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _complete_retryable(
        self,
        tenant_id: str,
        claimed: _ClaimedDispatch,
        error: RetryableToolDispatchError,
        now: datetime,
    ) -> str:
        session = await self._session_factory(tenant_id)
        try:
            message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.id == claimed.outbox_id,
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.status == "leased",
                    OutboxMessage.lease_owner == self._worker_id,
                    OutboxMessage.lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if message is None:
                await session.rollback()
                return "stale"
            execution = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == claimed.invocation.execution_id,
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.status == "RUNNING",
                    ToolExecution.dispatch_lease_owner == self._worker_id,
                    ToolExecution.dispatch_lease_token == claimed.lease_token,
                )
                .with_for_update()
            )
            if execution is None:
                await session.rollback()
                return "stale"

            tool = TOOL_CATALOG.resolve(execution.tool_name, "v1")
            execution.error_code = error.code[:50]
            execution.error_message = str(error)[:2000]
            execution.dispatch_lease_owner = None
            execution.dispatch_lease_expires_at = None
            repository = OutboxRepository(session, tenant_id)
            if message.attempt_count < tool.max_attempts:
                jitter = max(0.0, min(1.0, self._jitter_source()))
                delay = self._retry_base_seconds * (2 ** (message.attempt_count - 1))
                next_attempt_at = now + timedelta(seconds=delay + jitter * self._retry_base_seconds)
                execution.status = "PENDING"
                service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
                await service._append_event(
                    execution.case_id,
                    "TOOL_EXECUTION_RETRY_SCHEDULED",
                    {
                        "tool": execution.tool_name,
                        "attempt": message.attempt_count,
                        "error_code": execution.error_code,
                    },
                    agent_run_id=execution.agent_run_id,
                )
                released = await repository.schedule_retry(
                    message.id,
                    self._worker_id,
                    claimed.lease_token,
                    next_attempt_at,
                    execution.error_code,
                )
                if not released:
                    await session.rollback()
                    return "stale"
                await session.commit()
                return "retry_scheduled"

            service = HRCaseService(session, tenant_id, actor=f"system:dispatcher:{self._worker_id}")
            await service.finish_tool_execution(
                execution.id,
                ok=False,
                error_code=execution.error_code,
                error_message=execution.error_message,
            )
            await service.transition_case(execution.case_id, case_state.FAILED, reason=execution.error_code)
            dead_lettered = await repository.mark_terminal(
                message.id,
                self._worker_id,
                claimed.lease_token,
                execution.error_code,
                dead_letter=True,
            )
            if not dead_lettered:
                await session.rollback()
                return "stale"
            await session.commit()
            return "dead_letter"
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _is_currently_authorized(
        self,
        session: AsyncSession,
        tenant_id: str,
        execution: ToolExecution,
        grant: ExecutionGrant,
        now: datetime,
    ) -> bool:
        if grant.status not in {"active", "consumed"} or grant.expires_at <= now:
            return False
        if (
            grant.subject_type != "user"
            or grant.object_type != "hr_case"
            or grant.object_id != execution.case_id
            or grant.allowed_action != "execute"
            or grant.normalized_params_hash != execution.input_hash
        ):
            return False
        tool = TOOL_CATALOG.resolve(execution.tool_name, "v1")
        if grant.allowed_tool != tool.name or grant.capability != tool.required_capability:
            return False
        user = await session.scalar(
            select(User).where(
                User.id == grant.subject_id,
                User.tenant_id == tenant_id,
            )
        )
        if user is None or tool.required_capability not in ROLE_CAPABILITIES.get(user.role, set()):
            return False
        case = await session.scalar(select(HRCase).where(HRCase.id == execution.case_id, HRCase.tenant_id == tenant_id))
        if case is None:
            return False
        if user.role == "hrbp":
            return case.created_by == user.id or case.owner_id == user.id
        if user.role != "hr_manager":
            return False
        if case.created_by == user.id or case.owner_id == user.id:
            return True
        creator = await session.scalar(
            select(User).where(
                User.id == case.created_by,
                User.tenant_id == tenant_id,
            )
        )
        if creator is None or creator.org_unit_id is None:
            return False
        scope = await session.scalar(
            select(ManagerOrgScope.id).where(
                ManagerOrgScope.tenant_id == tenant_id,
                ManagerOrgScope.manager_user_id == user.id,
                ManagerOrgScope.org_unit_id == creator.org_unit_id,
            )
        )
        return scope is not None
