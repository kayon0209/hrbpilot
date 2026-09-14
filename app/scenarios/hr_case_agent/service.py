"""HR Case service layer (Phase 4) — the ONLY writer of case state.

Rules enforced here (API layer cannot bypass them):
  - every query and mutation is scoped to the caller's tenant
  - status changes go through the state machine (state.transition)
  - case events are append-only, seq is per-case monotonic
  - approvals must be APPROVED and unexpired before execution; a consumed
    approval cannot be reused (idempotency at the side-effect boundary)
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.hr_case import (
    AgentRun,
    ApprovalRequest,
    CaseEvent,
    CasePlan,
    HRCase,
    ToolExecution,
)
from app.data.models.user import User
from app.scenarios.hr_case_agent import state as case_state
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG
from app.shared.errors import AppError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)


class CasePermissionDeniedError(AppError):
    """Caller lacks the role required for this case action."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CASE_PERMISSION_DENIED", status_code=403)


class ApprovalError(AppError):
    """Approval is missing, expired, rejected, or already consumed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="APPROVAL_INVALID", status_code=409)


class HighRiskWriteBlockedError(AppError):
    """A high-risk case forbids write tools — evidence-only plus handoff."""

    def __init__(self, category: str, risk_level: str, tool_name: str) -> None:
        super().__init__(
            f"High-risk case ({category}/{risk_level}) forbids write tool {tool_name}; evidence-only + human handoff",
            code="HIGH_RISK_WRITE_BLOCKED",
            status_code=409,
        )


# Roles allowed to decide approvals and execute write tools. The HR Case
# Agent itself never holds these roles — it only proposes.
# Approvals are decided by the HR business role only. The platform admin
# deliberately holds no HR business capability (spec §3.2), so it must not
# decide HR case approvals either (P0-05: single source of truth = the RBAC
# capability matrix).
DECIDER_ROLES = frozenset({"hr_manager"})

# Phase 5 tool whitelist: write tools ALWAYS require an approval request;
# read tools do not. begin_tool_execution enforces this split.
WRITE_TOOLS = frozenset(tool.name for tool in TOOL_CATALOG.tools if tool.kind.value == "write")
READ_TOOLS = frozenset(tool.name for tool in TOOL_CATALOG.tools if tool.kind.value == "read")


def _parse_user_actor(actor: str) -> tuple[str | None, str | None]:
    """Parse the authenticated user audit label; non-user actors are internal."""
    if not actor.startswith("user:"):
        return None, None
    parts = dict(part.split(":", 1) for part in actor.split("|") if ":" in part)
    user_id = parts.get("user")
    role = parts.get("role")
    return (user_id or None, role or None)


class HRCaseService:
    """Tenant-safe service for the HR Case lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: str,
        actor: str = "system",
        visible_user_ids: set[str] | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.actor = actor
        self.visible_user_ids = visible_user_ids
        self.actor_id, self.actor_role = _parse_user_actor(actor)

    # --- case CRUD ---

    async def create_case(
        self,
        created_by: str,
        subject_ref: str,
        category: str,
        title: str,
        description: str | None = None,
        risk_level: str = "LOW",
        case_id: str | None = None,
    ) -> HRCase:
        case = HRCase(
            id=case_id,
            tenant_id=self.tenant_id,
            created_by=created_by,
            subject_ref=subject_ref,
            category=category,
            title=title,
            description=description,
            risk_level=risk_level,
            status=case_state.NEW,
        )
        self.session.add(case)
        await self.session.flush()
        await self._append_event(case.id, "CASE_CREATED", {"title": title, "risk_level": risk_level})
        return case

    async def assign_case_owner(self, case_id: str, owner_id: str) -> HRCase:
        """Assign a visible tenant user and append a durable ownership event."""
        case = await self.get_case(case_id)
        visible_user_ids = self._visible_creator_ids()
        if visible_user_ids is not None and owner_id not in visible_user_ids:
            raise CasePermissionDeniedError("Owner is outside the actor's visible scope")
        owner = await self.session.scalar(select(User).where(User.id == owner_id, User.tenant_id == self.tenant_id))
        if owner is None:
            raise NotFoundError("User", owner_id)
        if case.owner_id == owner_id:
            return case
        previous_owner_id = case.owner_id
        case.owner_id = owner_id
        await self._append_event(
            case.id,
            "CASE_OWNER_ASSIGNED",
            {"from": previous_owner_id, "to": owner_id},
        )
        return case

    async def get_case(self, case_id: str) -> HRCase:
        statement = select(HRCase).where(HRCase.id == case_id, HRCase.tenant_id == self.tenant_id)
        visible_creator_ids = self._visible_creator_ids()
        if visible_creator_ids is not None:
            if self.actor_id is not None:
                statement = statement.where(
                    or_(HRCase.created_by.in_(visible_creator_ids), HRCase.owner_id == self.actor_id)
                )
            else:
                statement = statement.where(HRCase.created_by.in_(visible_creator_ids))
        case = (await self.session.execute(statement)).scalars().first()
        if case is None:
            raise NotFoundError("HR case", case_id)
        return case

    async def search_cases(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
        category: str | None = None,
    ) -> list[HRCase]:
        """按 ACL 列出案件。

        与 ``get_case`` 共用 ``_visible_creator_ids`` 这一份可见性判据 —— 否则"能不能
        按 id 读"与"能不能在列表里看到"会漂移，而漂移的一侧通常是列表更宽
        （列表查询最容易只按租户过滤就交差）。这里刻意不写第二条判据。

        **不返回** ``description``：那是自由文本，通常含员工具体情况。列表的用途是
        "找到那个案件"，不是"读完它"；要看内容得按 id 走 ``get_case_summary``，
        那一步同样受 ACL 约束，但它是**针对性**的一次读取。
        """
        statement = select(HRCase).where(HRCase.tenant_id == self.tenant_id)
        visible_creator_ids = self._visible_creator_ids()
        if visible_creator_ids is not None:
            if self.actor_id is not None:
                statement = statement.where(
                    or_(HRCase.created_by.in_(visible_creator_ids), HRCase.owner_id == self.actor_id)
                )
            else:
                statement = statement.where(HRCase.created_by.in_(visible_creator_ids))
        if status:
            statement = statement.where(HRCase.status == status)
        if category:
            statement = statement.where(HRCase.category == category)
        statement = statement.order_by(HRCase.created_at.desc()).limit(max(1, min(limit, 50)))
        return list((await self.session.execute(statement)).scalars().all())

    async def get_approval(self, case_id: str, approval_id: str) -> ApprovalRequest:
        """读取一条审批。租户 + 案件双重限定，且案件本身也要过 ACL。

        先 ``get_case``（它做 ACL）再查审批，而不是直接按 approval_id 查 —— 后者
        只要有 id 就能读到，而 id 是会被猜的。
        """
        await self.get_case(case_id)  # ACL：看不见案件就看不见它的审批
        statement = select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.case_id == case_id,
            ApprovalRequest.tenant_id == self.tenant_id,
        )
        approval = (await self.session.execute(statement)).scalars().first()
        if approval is None:
            raise NotFoundError("Approval request", approval_id)
        return approval

    async def list_approvals(self, case_id: str) -> list[ApprovalRequest]:
        """某个案件的审批列表（同样先过案件的 ACL）。"""
        await self.get_case(case_id)
        statement = (
            select(ApprovalRequest)
            .where(ApprovalRequest.case_id == case_id, ApprovalRequest.tenant_id == self.tenant_id)
            .order_by(ApprovalRequest.created_at.desc())
        )
        return list((await self.session.execute(statement)).scalars().all())

    def _visible_creator_ids(self) -> set[str] | None:
        """Return a user actor's readable creators; internal actors stay explicit."""
        if self.actor_id is None or self.actor_role is None:
            return None
        if self.actor_role == "hrbp":
            return {self.actor_id}
        if self.actor_role == "hr_manager":
            return self.visible_user_ids or {self.actor_id}
        return set()

    async def transition_case(self, case_id: str, target: str, reason: str | None = None) -> HRCase:
        case = await self.get_case(case_id)
        previous = case.status
        case.status = case_state.transition(previous, target)
        await self._append_event(
            case.id, "STATUS_CHANGED", {"from": previous, "to": case.status, "reason": reason or ""}
        )
        return case

    # --- events (append-only) ---

    async def _append_event(
        self, case_id: str, event_type: str, payload: dict, agent_run_id: str | None = None
    ) -> None:
        # HRCASE-01: compute the per-case seq under a transaction-scoped
        # advisory lock so two concurrent appends cannot observe the same
        # max(seq) and both write seq N (unique constraint blow-up or lost
        # ordering). The lock is held for the duration of this transaction.
        # SQLite has no advisory locks; the SQLite tests are single-threaded
        # and the sequence logic itself is identical.
        if self.session.bind and self.session.bind.dialect.name != "sqlite":
            import hashlib

            from sqlalchemy import text

            case_lock = int(hashlib.sha256(f"{self.tenant_id}:{case_id}:events".encode()).hexdigest()[:15], 16)
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": case_lock})
        seq = (
            await self.session.execute(
                select(func.coalesce(func.max(CaseEvent.seq), 0)).where(CaseEvent.case_id == case_id)
            )
        ).scalar_one()
        self.session.add(
            CaseEvent(
                tenant_id=self.tenant_id,
                case_id=case_id,
                agent_run_id=agent_run_id,
                seq=int(seq) + 1,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
                actor=self.actor,
            )
        )
        await self.session.flush()

    async def list_events(self, case_id: str) -> list[CaseEvent]:
        await self.get_case(case_id)  # tenant check
        rows = (
            (
                await self.session.execute(
                    select(CaseEvent)
                    .where(CaseEvent.tenant_id == self.tenant_id, CaseEvent.case_id == case_id)
                    .order_by(CaseEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    # --- plans ---

    async def save_plan(
        self,
        case_id: str,
        steps: list[dict],
        rationale: str | None = None,
        risk_notes: str | None = None,
        agent_run_id: str | None = None,
    ) -> CasePlan:
        case = await self.get_case(case_id)
        # Drive any pre-plan states first: NEW → TRIAGED → EVIDENCE_READY.
        while case.status != case_state.EVIDENCE_READY:
            previous = case.status
            case.status = case_state.transition(
                previous, case_state.TRIAGED if previous == case_state.NEW else case_state.EVIDENCE_READY
            )
            await self._append_event(
                case_id,
                "STATUS_CHANGED",
                {"from": previous, "to": case.status, "reason": "plan created"},
                agent_run_id=agent_run_id,
            )
        plan = CasePlan(
            tenant_id=self.tenant_id,
            case_id=case_id,
            agent_run_id=agent_run_id,
            steps_json=json.dumps(steps, ensure_ascii=False),
            rationale=rationale,
            risk_notes=risk_notes,
        )
        self.session.add(plan)
        await self.session.flush()
        case.status = case_state.PLAN_READY
        await self._append_event(
            case_id, "PLAN_CREATED", {"plan_id": plan.id, "steps": len(steps)}, agent_run_id=agent_run_id
        )
        return plan

    # --- approvals (human-only decisions) ---

    async def request_approval(
        self,
        case_id: str,
        tool_name: str,
        params: dict,
        plan_id: str | None = None,
        agent_run_id: str | None = None,
        ttl_seconds: int = 3600,
        requester_user_id: str | None = None,
        client_id: str | None = None,
        installation_id: str | None = None,
    ) -> ApprovalRequest:
        case = await self.get_case(case_id)
        if tool_name in WRITE_TOOLS:
            from app.scenarios.hr_case_agent.planner import requires_human_review

            if requires_human_review(case.category, case.risk_level):
                # High-risk cases are evidence-only: no write approval can be
                # requested, so the agent loop hands off instead of escalating.
                raise HighRiskWriteBlockedError(case.category, case.risk_level, tool_name)
        # Normalize FIRST: the stored params carry schema defaults, the
        # execution-side re-validation then produces the identical dict, and
        # the canonical hash computed from it is what makes dedupe possible.
        from app.scenarios.hr_case_agent.tools import validate_tool_call

        try:
            normalized = validate_tool_call(tool_name, params)
        except Exception as e:
            raise ApprovalError(f"Invalid params for {tool_name}: {e}") from e
        input_hash = _hash_params(tool_name, normalized)

        reusable = await self._find_reusable_approval(case_id, tool_name, input_hash)
        if reusable is not None:
            # Same case + same tool + same params + still-unexpired PENDING:
            # reuse that record instead of creating another. Without this the
            # duplicate falls through to the state machine, which cannot move
            # AWAITING_APPROVAL → AWAITING_APPROVAL, so the caller got a raw
            # "Cannot transition case from AWAITING_APPROVAL to
            # AWAITING_APPROVAL" instead of the approval they already had.
            logger.info("approval_request_reused", case_id=case_id, tool=tool_name, approval_id=reusable.id)
            return reusable

        case.status = case_state.transition(case.status, case_state.AWAITING_APPROVAL)
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        params_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        approval = ApprovalRequest(
            tenant_id=self.tenant_id,
            case_id=case_id,
            plan_id=plan_id,
            tool_name=tool_name,
            params_json=params_json,
            input_hash=input_hash,
            requested_by=agent_run_id,
            expires_at=expires,
            requester_user_id=requester_user_id,
            client_id=client_id,
            installation_id=installation_id,
        )
        self.session.add(approval)
        await self.session.flush()
        await self._append_event(
            case_id,
            "APPROVAL_REQUESTED",
            {"approval_id": approval.id, "tool": tool_name},
            agent_run_id=agent_run_id,
        )
        return approval

    async def _find_reusable_approval(self, case_id: str, tool_name: str, input_hash: str) -> ApprovalRequest | None:
        """本案件上仍未过期、参数完全相同的待审批记录。

        只按 `input_hash` 判定：它已经是「工具 + 规范化参数」的 sha256，且与
        执行侧（`app/tools/gateway.py`、`app/outbox/dispatcher.py`）用的是同一个
        值 —— 复用的记录必然也是执行侧会认可的记录，不存在"复用了却执行不了"。
        过期判定放在 Python 侧做，避免不同方言对带时区时间的比较差异。
        """
        candidates = (
            (
                await self.session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.tenant_id == self.tenant_id,
                        ApprovalRequest.case_id == case_id,
                        ApprovalRequest.tool_name == tool_name,
                        ApprovalRequest.input_hash == input_hash,
                        ApprovalRequest.status == "PENDING",
                    )
                    .order_by(ApprovalRequest.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        for candidate in candidates:
            if candidate.expires_at is None or _as_utc(candidate.expires_at) > now:
                return candidate
        return None

    async def decide_approval(
        self,
        case_id: str,
        approval_id: str,
        approver_id: str,
        decision: str,
        reason: str | None,
        role: str,
    ) -> ApprovalRequest:
        if role not in DECIDER_ROLES:
            raise CasePermissionDeniedError(f"Role {role} cannot decide approvals")
        await self.get_case(case_id)
        approval = (
            (
                await self.session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id == approval_id,
                        ApprovalRequest.case_id == case_id,
                        ApprovalRequest.tenant_id == self.tenant_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if approval is None:
            raise NotFoundError("Approval request", approval_id)

        # 发起这条审批的安装实例若已被撤销，就不放行。
        #
        # 为什么必须在这里判：撤销授权时用户/运维期望的是"这个 Agent 从此不能再动我的
        # 东西"。若只挡住新调用，那条已经躺在队列里的写操作在几小时后的审批通过时
        # 照样会执行 —— 撤销看起来生效了，实际上漏掉了最要紧的那一个。
        if approval.installation_id and await self._grant_is_revoked(approval.installation_id):
            logger.warning(
                "approval_blocked_installation_revoked",
                approval_id=approval_id,
                case_id=case_id,
                installation_id=approval.installation_id,
            )
            raise CasePermissionDeniedError(
                "发起这条审批的授权已被撤销，因此不能再批准或执行它。请重新发起授权并提交新的审批。"
            )

        # Expiry is evaluated and PERSISTED atomically BEFORE any decision can
        # race it.  An expired approval becomes EXPIRED in the database and the
        # concurrent decision is rejected; the EXPIRED transition is durable and
        # is never rolled back with the raised error.
        if approval.expires_at is not None and datetime.now(UTC) > approval.expires_at.replace(tzinfo=UTC):
            expired = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(ApprovalRequest)
                    .where(
                        ApprovalRequest.id == approval_id,
                        ApprovalRequest.status == "PENDING",
                    )
                    .values(
                        status="EXPIRED",
                        approver_id=approver_id,
                        decided_at=datetime.now(UTC),
                    )
                ),
            )
            if expired.rowcount == 1:
                await self.session.commit()
                try:
                    await self._append_event(
                        case_id, "APPROVAL_DECIDED", {"approval_id": approval_id, "decision": "expired"}
                    )
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()
            raise ApprovalError(f"Approval {approval_id} expired")

        # Atomic decision: whichever of approve/reject commits first wins the
        # single status migration; every other racer sees 0 rows updated and is
        # rejected.  This is the DB-level guarantee that approve-vs-reject and
        # approve-vs-expire have exactly one winner.
        now = datetime.now(UTC)
        target = "APPROVED" if decision == "approve" else "REJECTED" if decision == "reject" else None
        if target is None:
            raise ApprovalError(f"Unknown decision: {decision}")

        updated = cast(
            CursorResult[Any],
            await self.session.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.status == "PENDING",
                )
                .values(
                    status=target,
                    approver_id=approver_id,
                    decision_reason=reason,
                    decided_at=now,
                )
            ),
        )
        if updated.rowcount != 1:
            # A racer already moved the approval (approved/rejected/consumed).
            await self.session.rollback()
            current = (
                (
                    await self.session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.id == approval_id,
                            ApprovalRequest.case_id == case_id,
                            ApprovalRequest.tenant_id == self.tenant_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            state = current.status if current else "missing"
            raise ApprovalError(f"Approval {approval_id} is {state}, not PENDING")

        await self.session.refresh(approval)
        await self._append_event(
            approval.case_id,
            "APPROVAL_DECIDED",
            {"approval_id": approval.id, "decision": decision, "reason": reason or ""},
        )
        return approval

    # --- tool execution (idempotent, approval-gated) ---

    async def _grant_is_revoked(self, installation_id: str) -> bool:
        """该安装实例（授权会话）是否已被撤销。

        复用 AS/RS 共用的撤销判据（``oauth_revocations.is_revoked``），只按 family
        粒度查 —— 审批绑定的是**授权会话**，不是某一把具体的 access token。
        另写一条查询会让"撤销"在两处各有一个定义，而它们迟早会不一致。
        """
        from app.data.repositories.oauth_revocations import is_revoked

        return await is_revoked(self.session, jti="", family_id=installation_id)

    async def begin_tool_execution(
        self,
        case_id: str,
        tool_name: str,
        params: dict,
        request_id: str,
        approval_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> ToolExecution:
        """Create the execution record for an approved write tool.

        Idempotency: a duplicate (case_id, request_id) returns the existing
        record without re-running the side effect. Approval: write tools
        require an APPROVED, unexpired, unconsumed approval request.
        """
        case = await self.get_case(case_id)
        if tool_name in WRITE_TOOLS:
            from app.scenarios.hr_case_agent.planner import requires_human_review

            if requires_human_review(case.category, case.risk_level):
                raise HighRiskWriteBlockedError(case.category, case.risk_level, tool_name)
        existing = (
            (
                await self.session.execute(
                    select(ToolExecution).where(
                        ToolExecution.case_id == case_id,
                        ToolExecution.request_id == request_id,
                        ToolExecution.tenant_id == self.tenant_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            # Only a SUCCEEDED execution is truly done. A FAILED row under a
            # CONSUMED approval must not be silently re-executed — retrying
            # requires a FRESH approval (Phase 7 demo uncovered this).
            if existing.status == "SUCCEEDED":
                return existing
            if tool_name in WRITE_TOOLS:
                raise ApprovalError(
                    f"Execution {request_id} previously {existing.status}; obtain a new approval and a new request_id to retry"
                )
            return existing

        # Approval↔execution binding compares the schema-normalized form
        # (defaults included) so a re-validated call always hashes equal.
        try:
            from app.scenarios.hr_case_agent.tools import validate_tool_call

            normalized_for_hash = validate_tool_call(tool_name, params)
        except Exception:
            normalized_for_hash = params
        input_hash = _hash_params(tool_name, normalized_for_hash)

        if tool_name in WRITE_TOOLS:
            if approval_id is None:
                raise ApprovalError(f"Write tool {tool_name} requires an approval request")
            approval = (
                (
                    await self.session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.id == approval_id,
                            ApprovalRequest.case_id == case_id,
                            ApprovalRequest.tenant_id == self.tenant_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if approval is None:
                raise NotFoundError("Approval request", approval_id)
            if approval.status == "EXPIRED" or (
                approval.expires_at is not None and datetime.now(UTC) > approval.expires_at.replace(tzinfo=UTC)
            ):
                raise ApprovalError(f"Approval {approval_id} expired")
            if approval.status != "APPROVED":
                raise ApprovalError(f"Tool {tool_name} requires APPROVED status, got {approval.status}")
            # Byte-for-byte params identity: the approval's stored params and
            # the executed params must hash to the same input_hash.
            if approval.input_hash is not None and approval.input_hash != input_hash:
                raise ApprovalError("Approval does not match the executed params")
            claimed = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(ApprovalRequest)
                    .where(
                        ApprovalRequest.id == approval_id,
                        ApprovalRequest.case_id == case_id,
                        ApprovalRequest.tenant_id == self.tenant_id,
                        ApprovalRequest.status == "APPROVED",
                        or_(ApprovalRequest.expires_at.is_(None), ApprovalRequest.expires_at > func.now()),
                    )
                    .values(status="CONSUMED")
                ),
            )
            if claimed.rowcount != 1:
                raise ApprovalError(f"Approval {approval_id} was already consumed or is no longer approved")
            await self.session.refresh(approval)

        execution = ToolExecution(
            tenant_id=self.tenant_id,
            case_id=case_id,
            approval_id=approval_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            request_id=request_id,
            input_hash=input_hash,
            status="RUNNING",
        )
        self.session.add(execution)
        await self.session.flush()
        await self._append_event(
            case_id,
            "TOOL_EXECUTION_STARTED",
            {"tool": tool_name, "request_id": request_id},
            agent_run_id=agent_run_id,
        )
        return execution

    async def finish_tool_execution(
        self,
        execution_id: str,
        ok: bool,
        result_summary: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ToolExecution:
        execution = (
            (
                await self.session.execute(
                    select(ToolExecution).where(
                        ToolExecution.id == execution_id, ToolExecution.tenant_id == self.tenant_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if execution is None:
            raise NotFoundError("Tool execution", execution_id)
        await self.get_case(execution.case_id)
        execution.status = "SUCCEEDED" if ok else "FAILED"
        execution.result_summary = result_summary
        execution.error_code = error_code
        execution.error_message = error_message
        await self._append_event(
            execution.case_id,
            "TOOL_EXECUTION_FINISHED",
            {"tool": execution.tool_name, "ok": ok, "error_code": error_code},
        )
        return execution

    # --- agent runs ---

    async def start_agent_run(self, case_id: str, goal: str) -> AgentRun:
        await self.get_case(case_id)
        run = AgentRun(tenant_id=self.tenant_id, case_id=case_id, goal=goal, status="RUNNING")
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_agent_run(
        self, run_id: str, status: str, steps_taken: int, tokens_used: int, handoff_reason: str | None = None
    ) -> AgentRun:
        run = (
            (
                await self.session.execute(
                    select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == self.tenant_id)
                )
            )
            .scalars()
            .first()
        )
        if run is None:
            raise NotFoundError("Agent run", run_id)
        await self.get_case(run.case_id)
        run.status = status
        run.steps_taken = steps_taken
        run.tokens_used = tokens_used
        run.handoff_reason = handoff_reason
        return run


def _hash_params(tool_name: str, params: dict) -> str:
    import hashlib

    blob = json.dumps({"tool": tool_name, "params": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Naive timestamps are stored as UTC; make that explicit for comparisons."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
