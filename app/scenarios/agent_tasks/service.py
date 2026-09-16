"""Agent 任务服务 —— prepare / provide / submit / status / list / cancel 的唯一实现。

单一实现、两个出口
------------------
MCP 任务端点（``/mcp/tasks``）、REST 桥与工作台路由都调用本服务；草稿、状态机、
幂等与投影因此只有一份语义。返回统一信封（``app.mcp.contract``），调用方不需要
在"协议出口"与"HTTP 出口"各维护一套分支 —— 两条出口结论不一致正是历史事故的根因。

提交必须对齐冻结草稿
--------------------
``submit_hr_action`` 只接受 task_id + draft_version + idempotency_key：业务参数
**不接受客户端重新提交**。提交前重算 ``input_hash`` 并核对 —— 用户确认的是服务端
冻结的那版草稿，而不是 Agent 当时临时拼出来的参数。并发/双击由行锁 + 草稿版本 +
幂等键三层挡住，同一动作只会产生一条审批。

撤销的覆盖面
------------
草稿与待审批阶段可取消；审批已被批准/消费后取消返回**真实进度**而不是伪装成功。
installation 撤销即时生效在两道既有闸上：令牌校验（撤销表按 family 查）挡住新的
调用，``HRCaseService`` 的审批决定闸挡住旧待审批的执行 —— 本服务不复制这两条判定。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.scopes import Scope
from app.data.models.agent_task import AgentTask, AgentTaskEvent
from app.data.models.hr_case import ApprovalRequest, HRCase, ToolExecution
from app.data.models.user import User
from app.mcp import budgets
from app.mcp.auth.principal import McpPrincipal
from app.mcp.contract import ToolOutcome, envelope, failure_envelope
from app.scenarios.agent_tasks import state as task_state
from app.scenarios.agent_tasks.intent import TASK_TYPES, CompileResult, compile_goal, normalized_params
from app.scenarios.hr_case_agent.service import DECIDER_ROLES, HRCaseService
from app.scenarios.hr_case_agent.tools import validate_tool_call
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 草稿冻结时长。过期不复活（hash 对应的是当时的语义），只允许复制为新草稿。
DRAFT_TTL = timedelta(hours=24)

#: submit 之后任务所处的状态：这些状态下重放同一 idempotency key 返回同一任务。
_POST_SUBMIT = frozenset(
    {task_state.AWAITING_APPROVAL, task_state.QUEUED, task_state.RUNNING, task_state.SUCCEEDED, task_state.FAILED}
)

_STATUS_OUTCOME: dict[str, ToolOutcome] = {
    task_state.INPUT_REQUIRED: ToolOutcome.INPUT_REQUIRED,
    task_state.READY_FOR_CONFIRMATION: ToolOutcome.AWAITING_CONFIRMATION,
    task_state.AWAITING_APPROVAL: ToolOutcome.AWAITING_APPROVAL,
    task_state.QUEUED: ToolOutcome.RUNNING,
    task_state.RUNNING: ToolOutcome.RUNNING,
    task_state.SUCCEEDED: ToolOutcome.SUCCEEDED,
    task_state.FAILED: ToolOutcome.FAILED,
    task_state.CANCELLED: ToolOutcome.CANCELLED,
    task_state.EXPIRED: ToolOutcome.EXPIRED,
    task_state.DRAFT: ToolOutcome.AWAITING_CONFIRMATION,
}

_NEXT_HINT: dict[str, str] = {
    task_state.INPUT_REQUIRED: "补齐缺失字段后再生成确认卡",
    task_state.DRAFT: "核对确认卡并提交审批",
    task_state.READY_FOR_CONFIRMATION: "核对确认卡并提交审批",
    task_state.AWAITING_APPROVAL: "等待 HR 经理在「团队待处理」中审批",
    task_state.QUEUED: "审批已通过，等待执行器受理",
    task_state.RUNNING: "执行中，稍后用 get_task_status 查询结果",
    task_state.SUCCEEDED: "任务完成，可查看产出",
    task_state.FAILED: "查看失败原因，必要时重新发起",
    task_state.CANCELLED: "任务已取消；需要时可复制为新草稿",
    task_state.EXPIRED: "草稿已过期；需要时重新发起",
}


class AgentTaskService:
    """tenant 与主体在构造时绑定：此后所有查询都无法逃逸出这个作用范围。"""

    def __init__(self, session: AsyncSession, principal: McpPrincipal, *, trace_id: str | None = None) -> None:
        self.session = session
        self.principal = principal
        self.tenant_id = principal.tenant_id
        self.trace_id = trace_id
        self._actor = f"user:{principal.user_id}|role:{principal.role}"

    # ------------------------------------------------------------------ #
    # 基础件
    # ------------------------------------------------------------------ #

    @staticmethod
    def _canonical_hash(task_type: str, params: dict[str, Any]) -> str:
        blob = json.dumps(
            {"task_type": task_type, "params": params}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _hr_service(self) -> HRCaseService:
        visible = self._visible_creator_ids
        return HRCaseService(self.session, self.tenant_id, actor=self._actor, visible_user_ids=visible)

    @property
    def _visible_creator_ids(self) -> set[str] | None:
        # 网关侧的案件可见性：发起这些写动作需要 hr_case 能力。案件服务内部再按
        # 对象级 ACL 过滤（hrbp 本人 / hr_manager 管辖范围），网关传「自己」即可，
        # 由 get_case 在执行期做最终判定。本属性只缩窄候选集，不做放宽。
        if self.principal.role in ("hrbp", "hr_manager"):
            return {self.principal.user_id}
        if self.principal.role == "employee":
            return set()
        return set()

    async def _validate_case_ref(self, case_ref: str) -> HRCase | None:
        """校验案件对当前身份可见。不可见与不存在返回同一个结果（None）。"""
        try:
            return await self._hr_service().get_case(case_ref)
        except AppError:
            return None

    async def _resolve_user_id(self, name: str) -> str | None:
        """姓名 → 唯一 user_id。不唯一或不存在都返回 None —— 绝不猜一个 id 进草稿。"""
        name = name.strip()
        if not name:
            return None
        rows = (
            await self.session.execute(
                select(User.id, User.name).where(
                    User.tenant_id == self.tenant_id,
                    User.is_active.is_(True),
                    (User.name == name) | (User.name.ilike(f"%{name}%")),
                )
            )
        ).all()
        exact = [row for row in rows if row.name == name]
        picked = exact if len(exact) == 1 else (rows if len(rows) == 1 else [])
        return picked[0].id if picked else None

    async def _append_event(
        self,
        task: AgentTask,
        event_type: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        seq = (
            await self.session.scalar(select(func.max(AgentTaskEvent.seq)).where(AgentTaskEvent.task_id == task.id))
            or 0
        )
        self.session.add(
            AgentTaskEvent(
                id=str(uuid.uuid4()),
                tenant_id=self.tenant_id,
                task_id=task.id,
                seq=seq + 1,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else None,
                actor=self._actor,
                trace_id=self.trace_id,
            )
        )
        await self.session.flush()

    async def _transition(
        self, task: AgentTask, target: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        current = task.status
        task_state.transition(current, target)
        task.status = target
        now = datetime.now(UTC)
        if target == task_state.CANCELLED:
            task.cancelled_at = now
        if target in (task_state.SUCCEEDED, task_state.FAILED):
            task.completed_at = now
        await self._append_event(
            task,
            event_type,
            from_status=current,
            to_status=target,
            payload=payload or {},
        )

    async def _load(self, task_id: str, *, for_update: bool = False) -> AgentTask | None:
        """按发起人加载任务。别人的任务与不存在返回同一个 None（不当探测器）。"""
        statement = select(AgentTask).where(
            AgentTask.tenant_id == self.tenant_id,
            AgentTask.id == task_id,
            AgentTask.requester_user_id == self.principal.user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalars().first()

    def _draft_output(self, task: AgentTask, *, missing: list[dict[str, str]], response_format: str) -> dict[str, Any]:
        spec = TASK_TYPES[task.task_type]
        payload: dict[str, Any] = {
            "task_id": task.id,
            "draft_version": task.draft_version,
            "status": task.status,
            "task_type": task.task_type,
            "interpreted_goal": task.interpreted_goal or task.goal_summary,
            "missing_fields": missing,
            "planned_steps": json.loads(task.planned_steps_json),
            "risk": {"level": task.risk_level, "reasons": json.loads(task.risk_reasons_json)},
            "required_scopes": [Scope.CASE_PROPOSE.value],
            "expires_at": task.expires_at.isoformat() if task.expires_at else None,
            "confirmation_summary": self._confirmation_summary(task, spec.planned_steps),
        }
        if response_format == "detailed":
            payload["canonical_params"] = json.loads(task.canonical_params_json)
        return payload

    @staticmethod
    def _confirmation_summary(task: AgentTask, steps: tuple[tuple[str, str], ...]) -> str:
        return (
            f"将提交 {len(steps)} 个步骤供 HR 审批；现在不会直接修改员工数据。"
            f"确认对象是版本 v{task.draft_version} 的冻结草稿（{task.input_hash[:12]}…）。"
        )

    # ------------------------------------------------------------------ #
    # prepare
    # ------------------------------------------------------------------ #

    async def prepare(
        self, goal: str, *, case_ref: str | None = None, response_format: str = "concise"
    ) -> dict[str, Any]:
        compiled = compile_goal(goal, case_ref=case_ref)
        if compiled.task_type is None:
            return failure_envelope("prepare_hr_action", "TASK_TYPE_UNDETECTED", tenant_id=self.tenant_id)
        params = self._enrich(compiled)
        missing = compiled.missing
        if "case_ref" in params:
            case = await self._validate_case_ref(params["case_ref"])
            if case is None:
                # 引用了不存在/不可见的案件：不创建带坏引用的草稿。
                return failure_envelope("prepare_hr_action", "NOT_FOUND", tenant_id=self.tenant_id)
            params["_case_category"] = case.category
            if case.risk_level == "HIGH":
                params["_high_risk"] = "true"
        status = task_state.INPUT_REQUIRED if missing else task_state.READY_FOR_CONFIRMATION
        spec = TASK_TYPES[compiled.task_type]
        now = datetime.now(UTC)
        task = AgentTask(
            id=str(uuid.uuid4()),
            tenant_id=self.tenant_id,
            requester_user_id=self.principal.user_id,
            client_id=self.principal.client_id,
            installation_id=str(self.principal.installation_id) if self.principal.installation_id else None,
            source_surface=self._source_surface(),
            task_type=compiled.task_type,
            goal_summary=(goal or "")[:500],
            interpreted_goal=compiled.interpreted_goal,
            input_hash=self._canonical_hash(compiled.task_type, params),
            draft_version=1,
            status=status,
            risk_level=compiled.risk_level,
            risk_reasons_json=json.dumps(compiled.risk_reasons, ensure_ascii=False),
            missing_fields_json=json.dumps(missing, ensure_ascii=False),
            planned_steps_json=json.dumps(
                [{"label": label, "effect": effect} for label, effect in spec.planned_steps], ensure_ascii=False
            ),
            expires_at=now + DRAFT_TTL,
            created_at=now,
            updated_at=now,
        )
        task.canonical_params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
        self.session.add(task)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            logger.exception("agent_task_prepare_failed", tenant_id=self.tenant_id)
            return failure_envelope("prepare_hr_action", "INTERNAL_ERROR", tenant_id=self.tenant_id)
        await self._append_event(
            task, "TASK_CREATED", to_status=status, payload={"missing": [m["field"] for m in missing]}
        )
        outcome = _STATUS_OUTCOME[status]
        return budgets.enforce(
            envelope(
                "prepare_hr_action",
                outcome,
                user_message=(
                    "草稿已生成，请核对确认卡后提交审批。"
                    if status == task_state.READY_FOR_CONFIRMATION
                    else "已创建草稿，还缺少必要信息，请先向用户确认。"
                ),
                **self._draft_output(task, missing=missing, response_format=response_format),
                tenant_id=self.tenant_id,
            )
        )

    def _enrich(self, compiled: CompileResult) -> dict[str, Any]:
        """编译产物 → 冻结草稿参数（键有序、无空白噪声）。派生键以 ``_`` 前缀标记，
        只允许服务端产生，客户端补参时不得写入 —— 见 provide_input 的过滤。"""
        if compiled.task_type is None:
            return {}
        return normalized_params(compiled.task_type, compiled.params)

    def _source_surface(self) -> str:
        from app.mcp.auth.principal import AuthMethod

        if self.principal.auth_method is AuthMethod.INTERNAL:
            return "web"
        client = (self.principal.client_id or "").lower()
        if "codex" in client or client.startswith("codex"):
            return "codex"
        if "workbuddy" in client:
            return "workbuddy"
        return "other"

    # ------------------------------------------------------------------ #
    # provide_task_input
    # ------------------------------------------------------------------ #

    async def provide_input(
        self, task_id: str, fields: dict[str, str], *, response_format: str = "concise"
    ) -> dict[str, Any]:
        task = await self._load(task_id, for_update=True)
        if task is None:
            return failure_envelope("provide_task_input", "TASK_NOT_FOUND", tenant_id=self.tenant_id)
        refusal = self._guard_editable(task, "provide_task_input")
        if refusal is not None:
            return refusal
        params: dict[str, Any] = json.loads(task.canonical_params_json)
        spec = TASK_TYPES[task.task_type]
        known = {f.key for f in spec.fields}
        for key, value in (fields or {}).items():
            if key not in known or key.startswith("_"):
                # 静默丢弃客户端不认识/不该认识的键？不 —— 返回可自纠的失败，
                # 让 Agent 学会只补缺失字段，而不是整包重发参数。
                return failure_envelope(
                    "provide_task_input",
                    "INVALID_PARAMS",
                    detail=f"字段 {key!r} 不可补充：只允许该任务类型的公开字段 {sorted(known)}。",
                    tenant_id=self.tenant_id,
                )
            if not isinstance(value, str):
                return failure_envelope(
                    "provide_task_input",
                    "INVALID_PARAMS",
                    detail=f"字段 {key!r} 必须是字符串。",
                    tenant_id=self.tenant_id,
                )
            params[key] = value.strip()

        if "case_ref" in fields:
            case = await self._validate_case_ref(params["case_ref"])
            if case is None:
                return failure_envelope("provide_task_input", "NOT_FOUND", tenant_id=self.tenant_id)
            params["_case_category"] = case.category
            if case.risk_level == "HIGH":
                params["_high_risk"] = "true"

        # 负责人/接收人：有名字而无 id 时尝试唯一解析；解析不了不猜测。
        if (
            spec.task_type in ("case_followup", "probation_followup")
            and params.get("owner")
            and not params.get("_owner_user_id")
        ):
            resolved = await self._resolve_user_id(params["owner"])
            if resolved:
                params["_owner_user_id"] = resolved
        if spec.task_type == "case_owner_change" and params.get("owner"):
            resolved = await self._resolve_user_id(params["owner"])
            if resolved:
                params["_owner_user_id"] = resolved
            else:
                return failure_envelope(
                    "provide_task_input",
                    "TASK_OWNER_UNRESOLVED",
                    detail=f"未找到唯一匹配的系统成员：{params['owner']}",
                    tenant_id=self.tenant_id,
                )
        if spec.task_type == "case_notification" and params.get("recipient_ref"):
            resolved = await self._resolve_user_id(params["recipient_ref"])
            if resolved:
                params["_recipient_user_id"] = resolved
            else:
                return failure_envelope(
                    "provide_task_input",
                    "TASK_OWNER_UNRESOLVED",
                    detail=f"通知接收人需要是系统内成员，未找到唯一匹配：{params['recipient_ref']}",
                    tenant_id=self.tenant_id,
                )

        cleaned = normalized_params(task.task_type, {k: v for k, v in params.items() if not k.startswith("_")})
        cleaned.update({k: v for k, v in params.items() if k.startswith("_")})
        missing = [m for m in json.loads(task.missing_fields_json) if not cleaned.get(m["field"])]
        status = task_state.INPUT_REQUIRED if missing else task_state.READY_FOR_CONFIRMATION

        task.canonical_params_json = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        task.input_hash = self._canonical_hash(task.task_type, cleaned)
        task.draft_version += 1
        task.missing_fields_json = json.dumps(missing, ensure_ascii=False)
        task.interpreted_goal = task.interpreted_goal or ""
        old_status = task.status
        if status != old_status:
            task_state.transition(old_status, status)
            task.status = status
        await self._append_event(
            task,
            "INPUT_PROVIDED",
            from_status=old_status,
            to_status=task.status,
            payload={
                "filled": sorted(fields.keys()),
                "still_missing": [m["field"] for m in missing],
                "draft_version": task.draft_version,
            },
        )
        outcome = _STATUS_OUTCOME[task.status]
        return budgets.enforce(
            envelope(
                "provide_task_input",
                outcome,
                user_message="补充完成，草稿已更新为新版本，请重新核对确认卡。",
                **self._draft_output(task, missing=missing, response_format=response_format),
                tenant_id=self.tenant_id,
            )
        )

    def _guard_editable(self, task: AgentTask, tool: str) -> dict[str, Any] | None:
        """草稿期闸：过期 → EXPIRED（持久），非草稿期 → 不可修改。返回拒绝信封或 None。"""
        if task.status in task_state.TERMINAL_STATUSES:
            return failure_envelope(tool, "TASK_NOT_EDITABLE", tenant_id=self.tenant_id, status=task.status)
        if self._expired(task) and task.status in task_state.DRAFTISH_STATUSES:
            self._mark_expired(task)
            return failure_envelope(tool, "TASK_EXPIRED", tenant_id=self.tenant_id)
        if task.status not in task_state.DRAFTISH_STATUSES:
            return failure_envelope(tool, "TASK_NOT_EDITABLE", tenant_id=self.tenant_id, status=task.status)
        return None

    @staticmethod
    def _expired(task: AgentTask) -> bool:
        return task.expires_at is not None and datetime.now(UTC) > task.expires_at

    def _mark_expired(self, task: AgentTask) -> None:
        """把草稿标记为过期。**幂等**：已经是终态时直接返回。

        这个短路是必需的，不是防御性编程：``submit()`` 的过期分支判的是
        ``expires_at`` 已过，对**已经 expired** 的任务会再走一次这里，而
        ``EXPIRED`` 是终态（``TRANSITIONS`` 为空集），``transition`` 会抛
        ``InvalidAgentTaskTransitionError`` —— 一次本应受控的「草稿已过期」
        拒绝会变成 INTERNAL_ERROR，而 INTERNAL_ERROR 的文案建议调用方重试，
        重试必然再次失败（外部 AI Agent 会因此陷入无效重试循环）。
        """
        if task_state.is_terminal(task.status):
            return
        task_state.transition(task.status, task_state.EXPIRED)
        task.status = task_state.EXPIRED

    # ------------------------------------------------------------------ #
    # submit
    # ------------------------------------------------------------------ #

    async def submit(self, task_id: str, draft_version: int, idempotency_key: str) -> dict[str, Any]:
        task = await self._load(task_id, for_update=True)
        if task is None:
            return failure_envelope("submit_hr_action", "TASK_NOT_FOUND", tenant_id=self.tenant_id)

        # 幂等重放：同一发起人 + 同一 key 再次提交，返回同一任务，不产生第二条审批。
        if task.submit_idempotency_key == idempotency_key and task.status in _POST_SUBMIT:
            return await self._submit_envelope(task, replayed=True)

        if task.status in _POST_SUBMIT:
            return failure_envelope(
                "submit_hr_action",
                "TASK_ALREADY_SUBMITTED",
                tenant_id=self.tenant_id,
                task_id=task.id,
                status=task.status,
            )
        if self._expired(task):
            self._mark_expired(task)
            await self._append_event(task, "TASK_EXPIRED", from_status=task.status)
            return failure_envelope("submit_hr_action", "TASK_EXPIRED", tenant_id=self.tenant_id, task_id=task.id)
        if task.status != task_state.READY_FOR_CONFIRMATION:
            return failure_envelope(
                "submit_hr_action",
                "TASK_NOT_SUBMITTABLE",
                tenant_id=self.tenant_id,
                status=task.status,
                missing_fields=json.loads(task.missing_fields_json),
            )
        if task.draft_version != draft_version:
            # 旧版本确认不得覆盖新草稿（文档 §6.2 draft_version 的存在理由）。
            return failure_envelope(
                "submit_hr_action",
                "TASK_STALE_VERSION",
                tenant_id=self.tenant_id,
                current_version=task.draft_version,
                submitted_version=draft_version,
            )
        params: dict[str, Any] = json.loads(task.canonical_params_json)
        if self._canonical_hash(task.task_type, params) != task.input_hash:
            # 冻结内容在库内被改写 —— 绝不放行，也不透出内部差异内容。
            logger.error("agent_task_hash_mismatch", task_id=task.id, tenant_id=self.tenant_id)
            return failure_envelope("submit_hr_action", "TASK_HASH_MISMATCH", tenant_id=self.tenant_id)

        spec = TASK_TYPES[task.task_type]
        case_ref = params.get("case_ref")
        if spec.requires_case and not case_ref:
            return failure_envelope(
                "submit_hr_action",
                "TASK_NOT_SUBMITTABLE",
                tenant_id=self.tenant_id,
                missing_fields=[{"field": "case_ref"}],
            )
        case = None
        if case_ref:
            case = await self._validate_case_ref(case_ref)
            if case is None:
                # 提交与准备之间案件可见性可能变化（对象级 ACL），以提交时为准。
                return failure_envelope("submit_hr_action", "NOT_FOUND", tenant_id=self.tenant_id)

        # 任务型工具中，底层审批工具的参数来自规范化草稿，但负责人/接收人
        # 的系统 id 需要在 prepare 阶段就解析；这里不再依赖 provide 才补派生字段。
        if (
            spec.task_type in ("case_followup", "probation_followup")
            and params.get("owner")
            and not params.get("_owner_user_id")
        ):
            resolved = await self._resolve_user_id(params["owner"])
            if resolved:
                params["_owner_user_id"] = resolved
        if spec.task_type == "case_owner_change" and params.get("owner") and not params.get("_owner_user_id"):
            resolved = await self._resolve_user_id(params["owner"])
            if resolved:
                params["_owner_user_id"] = resolved
        if (
            spec.task_type == "case_notification"
            and params.get("recipient_ref")
            and not params.get("_recipient_user_id")
        ):
            resolved = await self._resolve_user_id(params["recipient_ref"])
            if resolved:
                params["_recipient_user_id"] = resolved

        try:
            approval_params = self._approval_params(task, spec.approval_tool, params)
        except AppError as e:
            return failure_envelope("submit_hr_action", e.code, detail=None, tenant_id=self.tenant_id)

        service = self._hr_service()
        try:
            if case is not None:
                await self._ensure_plan_ready(service, case)
            approval = await service.request_approval(
                case_ref or "",
                tool_name=spec.approval_tool,
                params=approval_params,
                ttl_seconds=int(DRAFT_TTL.total_seconds()),
                requester_user_id=self.principal.user_id,
                client_id=self.principal.client_id,
                installation_id=str(self.principal.installation_id) if self.principal.installation_id else None,
            )
        except AppError as e:
            logger.warning("agent_task_submit_rejected", task_id=task.id, code=e.code, tenant_id=self.tenant_id)
            return failure_envelope("submit_hr_action", e.code, tenant_id=self.tenant_id)
        except Exception:
            logger.exception("agent_task_submit_crashed", task_id=task.id, tenant_id=self.tenant_id)
            return failure_envelope("submit_hr_action", "INTERNAL_ERROR", tenant_id=self.tenant_id)

        await self._transition(
            task,
            task_state.AWAITING_APPROVAL,
            "TASK_SUBMITTED",
            payload={"approval_id": approval.id, "draft_version": draft_version, "input_hash": task.input_hash[:12]},
        )
        task.approval_id = approval.id
        task.case_id = case_ref
        task.submitted_at = datetime.now(UTC)
        task.submit_idempotency_key = idempotency_key
        try:
            await self.session.flush()
        except IntegrityError:
            # 并发的同 key 提交：赢的那一笔已经改了任务状态，本事务回滚后重读。
            await self.session.rollback()
            winner = await self._load(task_id)
            if winner is not None:
                return await self._submit_envelope(winner, replayed=True)
            raise
        return await self._submit_envelope(task, replayed=False)

    async def _submit_envelope(self, task: AgentTask, *, replayed: bool) -> dict[str, Any]:
        payload = {
            "task_id": task.id,
            "status": task.status,
            "draft_version": task.draft_version,
            "approval_id": task.approval_id,
            "case_id": task.case_id,
            "next": {
                "poll": "get_task_status(task_id)",
                "web": f"/tasks?tab=agent&task_id={task.id}",
                "approval": (f"/team#case-{task.case_id}" if task.case_id else None),
            },
            "user_message_visible": True,
        }
        return budgets.enforce(
            envelope(
                "submit_hr_action",
                ToolOutcome.AWAITING_APPROVAL,
                user_message=(
                    ("该任务的这一版草稿已经提交过（幂等重放），没有创建第二条审批。" if replayed else "")
                    + "已提交 HR 审批，现在不会直接修改数据；可在任务中心或 get_task_status 继续跟踪。"
                ),
                **payload,
                tenant_id=self.tenant_id,
            )
        )

    def _approval_params(self, task: AgentTask, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """冻结草稿 → 现有审批工具的白名单参数。缺关键派生 id 时以受控错误挡下。"""
        spec = TASK_TYPES[task.task_type]
        title = params.get("title") or spec.title
        out: dict[str, Any]
        if tool == "create_work_task":
            out = {
                "title": title[:200],
                "next_action": (params.get("next_action") or params.get("title") or spec.title)[:1000],
            }
            owner_id = params.get("_owner_user_id")
            if owner_id:
                out["owner_user_id"] = owner_id
            elif params.get("owner"):
                out["waiting_for"] = f"由 {params['owner']} 负责跟进（系统内未唯一匹配，请审批时确认）"[:200]
            if params.get("due_at"):
                out["due_at"] = params["due_at"]
        elif tool == "assign_case_owner":
            owner_id = params.get("_owner_user_id")
            if not owner_id:
                raise AppError("负责人未解析", code="TASK_OWNER_UNRESOLVED", status_code=422)
            out = {"owner_id": owner_id}
        elif tool == "update_case_status":
            out = {"status": "RESOLVED"}
        elif tool == "send_case_notification":
            recipient = params.get("_recipient_user_id")
            if not recipient:
                raise AppError("接收人未解析", code="TASK_OWNER_UNRESOLVED", status_code=422)
            out = {
                "channel": "in_app",
                "recipient_ref": recipient,
                "template": params.get("template") or "followup",
                "params": {},
            }
        else:  # pragma: no cover - 白名单外类型在编译期即被注册表拦住
            raise AppError("不支持的任务类型", code="TASK_TYPE_UNDETECTED", status_code=422)
        # 过一遍现有工具的 schema：白名单、约束、默认值归一，与原子出口同一条校验。
        try:
            return validate_tool_call(tool, out)
        except Exception as e:  # ToolError
            raise AppError(str(e), code="INVALID_PARAMS", status_code=422) from e

    async def _ensure_plan_ready(self, service: HRCaseService, case: HRCase) -> None:
        """把案件推进到 PLAN_READY（审批闸要求的前置状态）。

        只走既有状态机的合法相邻路径（NEW→TRIAGED→EVIDENCE_READY→PLAN_READY），
        每一步都经 ``transition_case`` 留痕；跳不过去的中间态（AWAITING_APPROVAL、
        EXECUTING、终态）不强推，让 ``request_approval`` 给出真实的冲突拒绝。
        """
        path = {
            "NEW": ["TRIAGED", "EVIDENCE_READY", "PLAN_READY"],
            "TRIAGED": ["EVIDENCE_READY", "PLAN_READY"],
            "EVIDENCE_READY": ["PLAN_READY"],
        }
        hops = path.get(case.status)
        if not hops:
            return
        for target in hops:
            await service.transition_case(case.id, target, reason="外部 Agent 任务已编译并冻结计划")

    # ------------------------------------------------------------------ #
    # 状态投影 / 列表 / 取消
    # ------------------------------------------------------------------ #

    async def get_status(self, task_id: str, *, response_format: str = "concise") -> dict[str, Any]:
        task = await self._load(task_id, for_update=True)
        if task is None:
            return failure_envelope("get_task_status", "TASK_NOT_FOUND", tenant_id=self.tenant_id)
        await self._refresh_projection(task)
        payload = self._status_payload(task, response_format=response_format)
        return budgets.enforce(
            envelope("get_task_status", _STATUS_OUTCOME[task.status], **payload, tenant_id=self.tenant_id)
        )

    async def list_tasks(
        self, *, status: str | None = None, limit: int = 20, offset: int = 0, response_format: str = "concise"
    ) -> dict[str, Any]:
        statement = select(AgentTask).where(
            AgentTask.tenant_id == self.tenant_id,
            AgentTask.requester_user_id == self.principal.user_id,
        )
        if status:
            statement = statement.where(AgentTask.status == status)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = (
            (await self.session.execute(statement.order_by(AgentTask.updated_at.desc()).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        for row in rows:
            if not task_state.is_terminal(row.status):
                await self._refresh_projection(row, eager=False)
        items = [self._status_payload(row, response_format=response_format, brief=True) for row in rows]
        has_more = offset + limit < total
        return budgets.enforce(
            envelope(
                "list_my_tasks",
                ToolOutcome.FOUND if items else ToolOutcome.NO_EVIDENCE,
                user_message=(
                    f"共 {total} 个任务，本页 {len(items)} 个"
                    + ("；还有更多，按 next_cursor 继续。" if has_more else "。")
                ),
                items=items,
                total=total,
                truncated=has_more,
                next_cursor=str(offset + limit) if has_more else None,
                tenant_id=self.tenant_id,
            )
        )

    async def approval_queue(self, *, limit: int = 20) -> dict[str, Any]:
        """审批人的队列：本租户内停在 ``awaiting_approval`` 的 Agent 任务（**不限发起人**）。

        为什么不能复用 ``list_tasks``：那边按 ``requester_user_id`` 过滤，语义是"我发起的"；
        审批人要看的恰恰是**别人**发起、等待决定的任务 —— 两个视图的可见性方向相反。
        这里刻意**只做只读投影**（拿决定所需的 ``case_id`` / ``approval_id``）：
        写路径（补充信息 / 提交 / 取消）仍走 ``_load`` 的发起人 ACL，不因这个队列被绕过。
        """
        if self.principal.role not in DECIDER_ROLES:
            raise AppError("只有 HR 经理可以查看审批队列。", code="APPROVAL_DECIDER_REQUIRED", status_code=403)
        statement = (
            select(AgentTask, User.name)
            .join(User, (User.tenant_id == AgentTask.tenant_id) & (User.id == AgentTask.requester_user_id))
            .where(
                AgentTask.tenant_id == self.tenant_id,
                AgentTask.status == task_state.AWAITING_APPROVAL,
                AgentTask.approval_id.is_not(None),
                AgentTask.case_id.is_not(None),
            )
            .order_by(AgentTask.updated_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        items = [
            {
                "task_id": task.id,
                "title": task.interpreted_goal or task.goal_summary,
                "task_type": task.task_type,
                "risk_level": task.risk_level,
                "requester_name": requester_name,
                "approval_id": task.approval_id,
                "case_id": task.case_id,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                # 决定入口是既有业务接口 POST /api/v1/hr-cases/{case_id}/approve；
                # 队列只负责把两条 ID 送到决定人眼前，不另开一条批准通道。
                "web_path": f"/tasks?tab=agent&task_id={task.id}",
            }
            for task, requester_name in rows
        ]
        return budgets.enforce(
            envelope(
                "approval_queue",
                ToolOutcome.FOUND if items else ToolOutcome.NO_EVIDENCE,
                user_message=(f"共 {len(items)} 件待审批。" if items else "当前没有等待审批的办理。"),
                items=items,
                tenant_id=self.tenant_id,
            )
        )

    def _status_payload(self, task: AgentTask, *, response_format: str, brief: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": task.id,
            "task_type": task.task_type,
            "title": task.interpreted_goal or task.goal_summary,
            "status": task.status,
            "risk_level": task.risk_level,
            "source_surface": task.source_surface,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "next": _NEXT_HINT.get(task.status, ""),
            "user_view": {"web_path": f"/tasks?tab=agent&task_id={task.id}"},
        }
        if not brief:
            payload.update(
                {
                    "draft_version": task.draft_version,
                    "approval_id": task.approval_id,
                    "case_id": task.case_id,
                    "work_task_id": task.work_task_id,
                    "missing_fields": json.loads(task.missing_fields_json),
                    "planned_steps": json.loads(task.planned_steps_json),
                    "last_error_code": task.last_error_code,
                    "expires_at": task.expires_at.isoformat() if task.expires_at else None,
                }
            )
        return payload

    async def task_events(self, task_id: str) -> list[dict[str, Any]]:
        """详情的时间线（工作台抽屉用）。只含安全摘要字段。"""
        task = await self._load(task_id)
        if task is None:
            return []
        rows = (
            (
                await self.session.execute(
                    select(AgentTaskEvent)
                    .where(AgentTaskEvent.tenant_id == self.tenant_id, AgentTaskEvent.task_id == task.id)
                    .order_by(AgentTaskEvent.seq.asc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "seq": row.seq,
                "event_type": row.event_type,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "at": row.created_at.isoformat() if row.created_at else None,
                "payload": json.loads(row.payload_json) if row.payload_json else {},
            }
            for row in rows
        ]

    async def _refresh_projection(self, task: AgentTask, *, eager: bool = True) -> None:
        """把审批/执行的真实状态投影回任务状态机（投影只在允许迁移时推进）。"""
        if task.approval_id is None or task.status not in (
            task_state.AWAITING_APPROVAL,
            task_state.QUEUED,
            task_state.RUNNING,
        ):
            return
        approval = await self.session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == self.tenant_id, ApprovalRequest.id == task.approval_id
            )
        )
        if approval is None:
            return
        now = datetime.now(UTC)
        pending_expired = (
            approval.expires_at is not None and now > approval.expires_at.replace(tzinfo=UTC)
            if approval.expires_at
            else False
        )
        if approval.status == "PENDING":
            if pending_expired and task.status == task_state.AWAITING_APPROVAL:
                await self._transition(task, task_state.EXPIRED, "APPROVAL_EXPIRED")
            return
        if approval.status == "REJECTED":
            if task_state.can_transition(task.status, task_state.FAILED):
                task.last_error_code = "APPROVAL_REJECTED"
                await self._transition(task, task_state.FAILED, "APPROVAL_REJECTED", payload={"reason": "审批被驳回"})
            return
        if approval.status == "EXPIRED":
            if task_state.can_transition(task.status, task_state.EXPIRED):
                await self._transition(task, task_state.EXPIRED, "APPROVAL_EXPIRED")
            elif task_state.can_transition(task.status, task_state.FAILED):
                task.last_error_code = "APPROVAL_EXPIRED"
                await self._transition(task, task_state.FAILED, "APPROVAL_EXPIRED")
            return
        # APPROVED / CONSUMED：执行记录是唯一可信的进度源，不猜步数。
        execution = await self.session.scalar(
            select(ToolExecution).where(
                ToolExecution.tenant_id == self.tenant_id, ToolExecution.approval_id == approval.id
            )
        )
        if approval.status == "APPROVED" and execution is None:
            if task_state.can_transition(task.status, task_state.QUEUED):
                await self._transition(task, task_state.QUEUED, "APPROVAL_APPROVED")
            return
        if execution is None:
            return
        exec_status = (execution.status or "").upper()
        if exec_status in ("RUNNING", "PENDING", "QUEUED"):
            if task_state.can_transition(task.status, task_state.RUNNING):
                await self._transition(task, task_state.RUNNING, "EXECUTION_STARTED")
            return
        if exec_status in ("SUCCEEDED", "COMPLETED"):
            self._project_execution_refs(task, execution)
            if task_state.can_transition(task.status, task_state.SUCCEEDED):
                await self._transition(task, task_state.SUCCEEDED, "EXECUTION_SUCCEEDED")
            return
        if exec_status in ("FAILED", "HANDED_OFF"):
            task.last_error_code = execution.error_code or "EXECUTION_FAILED"
            if task_state.can_transition(task.status, task_state.FAILED):
                await self._transition(
                    task, task_state.FAILED, "EXECUTION_FAILED", payload={"error_code": task.last_error_code}
                )
            return
        logger.warning("agent_task_unknown_execution_status", task_id=task.id, status=exec_status)
        if not eager:
            return

    def _project_execution_refs(self, task: AgentTask, execution: ToolExecution) -> None:
        """执行成功后把真实产出的 id 投影回任务。

        New structured execution records carry a JSON result.  The governed
        dispatcher predates the task gateway and stores a concise text summary,
        so the one legacy ``create_work_task`` format is parsed narrowly too.
        It is emitted only by our controlled executor and must match the whole
        string; arbitrary summaries never become object references.
        """
        if not execution.result_summary:
            return
        try:
            result = json.loads(execution.result_summary)
        except (TypeError, ValueError):
            result = None
        if result is None:
            legacy_work_task = re.fullmatch(r"work task ([0-9a-f-]{36}) created", execution.result_summary)
            if legacy_work_task and execution.tool_name == "create_work_task":
                try:
                    task.work_task_id = str(uuid.UUID(legacy_work_task.group(1)))
                except ValueError:
                    pass
            return
        if not isinstance(result, dict):
            return
        if result.get("task_id"):
            task.work_task_id = str(result["task_id"])
        if result.get("case_id"):
            task.case_id = str(result["case_id"])
        if result.get("approval_id"):
            task.approval_id = str(result["approval_id"])

    async def cancel(self, task_id: str) -> dict[str, Any]:
        task = await self._load(task_id, for_update=True)
        if task is None:
            return failure_envelope("cancel_task", "TASK_NOT_FOUND", tenant_id=self.tenant_id)
        if task.status in (task_state.SUCCEEDED, task_state.FAILED):
            return failure_envelope("cancel_task", "TASK_NOT_CANCELLABLE", tenant_id=self.tenant_id, status=task.status)
        if task.status == task_state.CANCELLED:
            return envelope(
                "cancel_task",
                ToolOutcome.CANCELLED,
                task_id=task.id,
                status=task.status,
                user_message="任务此前已取消，未重复执行。",
                tenant_id=self.tenant_id,
            )
        if task.status == task_state.EXPIRED:
            return failure_envelope("cancel_task", "TASK_NOT_CANCELLABLE", tenant_id=self.tenant_id, status=task.status)
        if task.status == task_state.AWAITING_APPROVAL and task.approval_id:
            approval = await self.session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.tenant_id == self.tenant_id, ApprovalRequest.id == task.approval_id
                )
            )
            if approval is not None and approval.status in ("APPROVED", "CONSUMED"):
                # 已进入不可撤销阶段：返回真实进度，不伪装成功（文档 §6.3）。
                return failure_envelope(
                    "cancel_task",
                    "TASK_NOT_CANCELLABLE",
                    tenant_id=self.tenant_id,
                    status="awaiting_execution",
                    progress={"approval_status": approval.status},
                    user_message="无法取消：审批已通过，任务已执行到等待执行/执行中。",
                )
            if approval is not None and approval.status == "PENDING":
                # 只撤回**这条**审批本身（决定权仍在 HR 的驳回闸上）：网关撤销发起人的
                # 请求，把审批置为撤回终态，案件退回可再计划状态。
                approval.status = "WITHDRAWN"
                approval.decided_at = datetime.now(UTC)
                approval.decision_reason = "requester_cancelled"
                if task.case_id:
                    try:
                        service = self._hr_service()
                        case = await service.get_case(task.case_id)
                        if case.status == "AWAITING_APPROVAL":
                            await service.transition_case(case.id, "PLAN_READY", reason="外部 Agent 撤回了该办理请求")
                    except AppError:
                        pass
            await self._transition(task, task_state.CANCELLED, "TASK_CANCELLED", payload={"stage": "awaiting_approval"})
            return envelope(
                "cancel_task",
                ToolOutcome.CANCELLED,
                task_id=task.id,
                status=task.status,
                user_message="已在审批前撤回：任务不会执行，审批记录保留可追溯。",
                tenant_id=self.tenant_id,
            )
        if self._expired(task):
            self._mark_expired(task)
            await self._append_event(task, "TASK_EXPIRED", from_status=task.status)
            return failure_envelope("cancel_task", "TASK_EXPIRED", tenant_id=self.tenant_id, task_id=task.id)
        await self._transition(task, task_state.CANCELLED, "TASK_CANCELLED", payload={"stage": task.status})
        return budgets.enforce(
            envelope(
                "cancel_task",
                ToolOutcome.CANCELLED,
                task_id=task.id,
                status=task.status,
                user_message="任务已取消，未产生任何业务写入。",
                tenant_id=self.tenant_id,
            )
        )


#: ``_transition`` 里按目标状态补记的时间戳（表驱动，避免逐处 if）。
task_state.SUBMITTED_AT_STATUSES = {}  # type: ignore[attr-defined]
