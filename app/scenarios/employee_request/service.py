"""Employee request service (Phase 4) — the employee-visible service contract.

Two projections, one table (spec §5.4):
  - employee view: desensitized business status + next step. hr_note and
    hr_case_id never leave this module toward an employee.
  - HR triage view (hrbp/hr_manager with request capability): full row.

Employees can only see and act on their OWN requests (object-level ACL);
one user can never enumerate another's requests even inside the same tenant.
"""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
from typing import cast

from pydantic import BaseModel, Field

from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

EMPLOYEE_STATUS_LABELS = {
    "submitted": "已提交",
    "needs_materials": "待补充",
    "in_progress": "处理中",
    "resolved": "已解决",
}

REQUEST_TYPE_LABELS = {
    "policy_check": "制度核对",
    "certificate": "证明开具",
    "process_help": "流程协助",
    "other": "其他事项",
}

CONNECTOR_PLATFORM_LABELS = {
    "wecom": "企业微信",
    "feishu": "飞书",
    "dingtalk": "钉钉",
}


class CreateRequestBody(BaseModel):
    request_type: str = Field(..., pattern="^(policy_check|certificate|process_help|other)$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)


class EmployeeRequestView(BaseModel):
    """The desensitized projection returned to the requesting employee."""

    request_id: str
    request_type: str
    request_type_label: str
    title: str
    status: str
    status_label: str
    next_step: str
    needs_materials: str | None = None
    updated_at: str | None = None
    created_at: str | None = None


class HrTriageBody(BaseModel):
    status: str = Field(..., pattern="^(needs_materials|in_progress|resolved)$")
    next_step_for_employee: str | None = Field(None, max_length=500)
    needs_materials: str | None = Field(None, max_length=1000)
    hr_note: str | None = Field(None, max_length=2000)
    hr_owner_id: str | None = Field(None, max_length=36)


class DeliveryAttemptView(BaseModel):
    """HR-only projection of a local protocol simulator delivery attempt."""

    attempt_id: str
    status: str
    attempt_count: int
    provider_msgid: str | None = None
    safe_message: str
    retryable: bool
    error: str | None = None


def _employee_view(row) -> EmployeeRequestView:
    status = row.status or "submitted"
    return EmployeeRequestView(
        request_id=row.id,
        request_type=row.request_type,
        request_type_label=REQUEST_TYPE_LABELS.get(row.request_type, row.request_type),
        title=row.title,
        status=status,
        status_label=EMPLOYEE_STATUS_LABELS.get(status, status),
        next_step=row.next_step_for_employee or "HR 会尽快处理；如需补充材料会在这里说明。",
        needs_materials=row.needs_materials,
        updated_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
        created_at=row.created_at.isoformat() if getattr(row, "created_at", None) else None,
    )


async def create_request(tenant_id: str, user_id: str, body: CreateRequestBody) -> EmployeeRequestView:
    """Employee files a request. No auto-triage, no auto-resolution."""
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = EmployeeRequest(
            tenant_id=tenant_id,
            created_by=user_id,
            request_type=body.request_type,
            title=body.title,
            description=body.description,
            status="submitted",
            next_step_for_employee="已提交，HR 会尽快查看；需要补充材料时会在这里说明。",
        )
        db.add(row)
        await db.commit()
        view = _employee_view(row)
    logger.info("employee_request_created", tenant_id=tenant_id, request_id=row.id, request_type=body.request_type)
    return view


async def list_my_requests(tenant_id: str, user_id: str) -> list[EmployeeRequestView]:
    """Only the caller's OWN requests — object-level ACL, newest first."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(EmployeeRequest)
                    .where(EmployeeRequest.tenant_id == tenant_id, EmployeeRequest.created_by == user_id)
                    .order_by(EmployeeRequest.updated_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    return [_employee_view(row) for row in rows]


async def get_my_request(tenant_id: str, user_id: str, request_id: str) -> EmployeeRequestView:
    view = await _load_owned(tenant_id, user_id, request_id)
    return view


async def hr_triage(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    request_id: str,
    body: HrTriageBody,
    *,
    gateway=None,
) -> dict:
    """HR updates the business status and the employee-facing next step.

    The internal note is stored but NEVER returned to the employee; the
    employee only sees the mapped status and next step (spec §7.9).
    """
    from datetime import datetime as dt

    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.shared.audit import append_security_audit_event

    if body.status == "needs_materials" and not body.needs_materials:
        raise ValidationError("请求补充材料时需要说明缺什么")
    if body.status in ("in_progress", "resolved") and not body.next_step_for_employee:
        raise ValidationError("需要给员工一个明确的下一步说明")

    factory = get_session_factory()
    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        filters = [EmployeeRequest.tenant_id == tenant_id, EmployeeRequest.id == request_id]
        if actor_role == "hrbp":
            filters.append(EmployeeRequest.hr_owner_id == actor_id)
        elif actor_role == "hr_manager":
            filters.append(EmployeeRequest.created_by.in_(visible_user_ids))
        else:
            raise NotFoundError("Request", request_id)
        row = (await db.execute(select(EmployeeRequest).where(*filters))).scalars().first()
        if row is None:
            raise NotFoundError("Request", request_id)
        row.status = body.status
        row.next_step_for_employee = (body.next_step_for_employee or "")[:500] or None
        row.needs_materials = (body.needs_materials or "")[:1000] or None
        row.hr_note = (body.hr_note or "")[:2000] or None
        if body.hr_owner_id:
            if actor_role != "hr_manager":
                raise ValidationError("只有授权范围内的 HR 经理可以分配负责人")
            owner = (
                (
                    await db.execute(
                        select(User).where(
                            User.tenant_id == tenant_id,
                            User.id == body.hr_owner_id,
                            User.role == "hrbp",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if owner is None or owner.id not in visible_user_ids:
                raise ValidationError("负责人不在你的授权组织范围内")
            row.hr_owner_id = owner.id
        if body.status == "resolved":
            row.resolved_at = dt.now(UTC)
        row.updated_at = dt.now(UTC)
        attempt_id = await enqueue_wecom_delivery(db, tenant_id, row, body)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="employee_request.triaged",
            object_type="employee_request",
            object_id=request_id,
            details={"status": body.status, "owner_id": row.hr_owner_id},
        )
        await db.commit()
        view = _employee_view(row)
    logger.info("employee_request_triaged", tenant_id=tenant_id, request_id=request_id, status=body.status)
    # HR sees the desensitized employee view plus the internal note (their own).
    delivery = await deliver_wecom_attempt(tenant_id, attempt_id, gateway=gateway) if attempt_id else None
    return {
        "request": view.model_dump(),
        "hr_note": body.hr_note,
        "delivery": delivery.model_dump() if delivery else None,
    }


def _delivery_view(row) -> DeliveryAttemptView:
    return DeliveryAttemptView(
        attempt_id=row.id,
        status=row.status,
        attempt_count=row.attempt_count,
        provider_msgid=row.provider_msgid,
        safe_message=row.message_content,
        retryable=row.status == "retryable_failed",
        error=row.last_error,
    )


def _delivery_digest(*, source_id: str, recipient_ref: str, content: str, status: str) -> str:
    """Digest employee-visible business state only, never the HR internal note."""
    material = "\x1f".join((source_id, recipient_ref, content, status))
    return sha256(material.encode("utf-8")).hexdigest()


async def enqueue_wecom_delivery(db, tenant_id: str, request, body: HrTriageBody) -> str | None:
    """Atomically insert one local-simulation outbox record with the triage change."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from app.data.models.connector import ConnectorDeliveryAttempt
    from app.data.models.data_source import DataSource

    if not request.connector_source_id or not request.external_sender_id:
        return None
    source = await db.scalar(
        select(DataSource).where(
            DataSource.tenant_id == tenant_id,
            DataSource.id == request.connector_source_id,
        )
    )
    if source is None or source.platform != "wecom" or source.event_route != "employee_request":
        return None
    content = body.needs_materials if body.status == "needs_materials" else body.next_step_for_employee
    if not content:
        return None
    statement = (
        insert(ConnectorDeliveryAttempt)
        .values(
            tenant_id=tenant_id,
            employee_request_id=request.id,
            source_id=source.id,
            channel="wecom_simulator",
            recipient_ref=request.external_sender_id,
            message_content=content,
            content_digest=_delivery_digest(
                source_id=source.id,
                recipient_ref=request.external_sender_id,
                content=content,
                status=body.status,
            ),
            status="queued",
        )
        .on_conflict_do_nothing(constraint="uq_connector_delivery_attempt_business_version")
        .returning(ConnectorDeliveryAttempt.id)
    )
    return cast(str | None, await db.scalar(statement))


async def deliver_wecom_attempt(tenant_id: str, attempt_id: str, *, gateway=None) -> DeliveryAttemptView:
    """Run one local-only delivery attempt after the triage transaction commits."""
    from datetime import datetime as dt

    from sqlalchemy import select

    from app.connectors.wecom_outbound import WeComOutboundSimulator
    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt
    from app.data.models.data_source import DataSource
    from app.shared.errors import NotFoundError

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        attempt = await db.scalar(
            select(ConnectorDeliveryAttempt)
            .where(
                ConnectorDeliveryAttempt.tenant_id == tenant_id,
                ConnectorDeliveryAttempt.id == attempt_id,
            )
            .with_for_update()
        )
        if attempt is None:
            raise NotFoundError("DeliveryAttempt", attempt_id)
        if attempt.status in {"simulated_accepted", "rejected"}:
            return _delivery_view(attempt)
        source = await db.scalar(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.id == attempt.source_id,
            )
        )
        now = dt.now(UTC)
        attempt.attempt_count += 1
        attempt.last_attempt_at = now

        if source is None or source.revoked_at is not None:
            attempt.status = "rejected"
            attempt.last_error = "来源已撤销，不能进行本地协议模拟"
        elif source.wecom_callback_config_encrypted is None:
            attempt.status = "rejected"
            attempt.last_error = "来源未配置企业微信入站回调，不能进行本地协议模拟"
        elif "/" in attempt.recipient_ref:
            attempt.status = "rejected"
            attempt.last_error = "未配置可发送的企业微信内部成员 ID"
        else:
            simulator = gateway or WeComOutboundSimulator()
            try:
                # These values are synthetic.  The encrypted inbound callback
                # bundle is intentionally never decrypted or reused as outbound
                # authorization by the local simulator.
                token = await simulator.get_token("local-simulator", "local-only")
                response = await simulator.send_text(token.value, "0", attempt.recipient_ref, attempt.message_content)
                if response.errcode == 42001:
                    token = await simulator.get_token("local-simulator", "local-only")
                    response = await simulator.send_text(
                        token.value, "0", attempt.recipient_ref, attempt.message_content
                    )
                attempt.provider_errcode = response.errcode
                attempt.provider_msgid = response.msgid
                if response.errcode == 0:
                    attempt.status = "simulated_accepted"
                    attempt.last_error = None
                    attempt.next_retry_at = None
                elif response.retryable:
                    attempt.status = "retryable_failed"
                    attempt.last_error = "本地协议模拟暂时不可用，可重试"
                    attempt.next_retry_at = now
                else:
                    attempt.status = "rejected"
                    attempt.last_error = "模拟平台拒绝该接收者" if response.invaliduser else "本地协议模拟拒绝发送"
            except ValueError as exc:
                attempt.status = "rejected"
                attempt.last_error = str(exc)
            except Exception:
                logger.exception("wecom_local_simulator_failed", tenant_id=tenant_id, attempt_id=attempt_id)
                attempt.status = "retryable_failed"
                attempt.last_error = "本地协议模拟暂时不可用，可重试"
                attempt.next_retry_at = now
        await db.commit()
        return _delivery_view(attempt)


async def retry_hr_delivery(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    request_id: str,
    attempt_id: str,
    *,
    gateway=None,
) -> DeliveryAttemptView:
    """Retry only a transient local simulation failure visible to this HR actor."""
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt
    from app.data.models.scenarios import EmployeeRequest

    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    if actor_role == "hrbp":
        scope_filter = EmployeeRequest.hr_owner_id == actor_id
    elif actor_role == "hr_manager":
        if not visible_user_ids:
            raise NotFoundError("Request", request_id)
        scope_filter = EmployeeRequest.created_by.in_(visible_user_ids)
    else:
        raise NotFoundError("Request", request_id)

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        request = await db.scalar(
            select(EmployeeRequest).where(
                EmployeeRequest.tenant_id == tenant_id,
                EmployeeRequest.id == request_id,
                scope_filter,
            )
        )
        if request is None:
            raise NotFoundError("Request", request_id)
        attempt = await db.scalar(
            select(ConnectorDeliveryAttempt)
            .where(
                ConnectorDeliveryAttempt.tenant_id == tenant_id,
                ConnectorDeliveryAttempt.id == attempt_id,
                ConnectorDeliveryAttempt.employee_request_id == request.id,
            )
            .with_for_update()
        )
        if attempt is None:
            raise NotFoundError("DeliveryAttempt", attempt_id)
        if attempt.status != "retryable_failed":
            raise ValidationError("只有本地模拟临时失败的回执可以重试")
        await db.commit()
    return await deliver_wecom_attempt(tenant_id, attempt_id, gateway=gateway)


async def hr_list_open(tenant_id: str, actor_id: str, actor_role: str) -> list[dict]:
    """Return only explicitly owned or manager-scoped open requests with source context."""
    from sqlalchemy import and_, select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorDeliveryAttempt
    from app.data.models.data_source import DataSource
    from app.data.models.scenarios import EmployeeRequest

    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    if actor_role == "hrbp":
        scope_filter = EmployeeRequest.hr_owner_id == actor_id
    elif actor_role == "hr_manager":
        if not visible_user_ids:
            return []
        scope_filter = EmployeeRequest.created_by.in_(visible_user_ids)
    else:
        return []

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(EmployeeRequest, DataSource.platform, DataSource.name)
                .outerjoin(
                    DataSource,
                    and_(
                        DataSource.tenant_id == EmployeeRequest.tenant_id,
                        DataSource.id == EmployeeRequest.connector_source_id,
                    ),
                )
                .where(
                    EmployeeRequest.tenant_id == tenant_id,
                    EmployeeRequest.status != "resolved",
                    scope_filter,
                )
                .order_by(EmployeeRequest.updated_at.desc())
                .limit(100)
            )
        ).all()
        request_ids = [row.id for row, _platform, _source_name in rows]
        deliveries_by_request: dict[str, object] = {}
        if request_ids:
            attempts = (
                await db.execute(
                    select(ConnectorDeliveryAttempt)
                    .where(
                        ConnectorDeliveryAttempt.tenant_id == tenant_id,
                        ConnectorDeliveryAttempt.employee_request_id.in_(request_ids),
                    )
                    .order_by(ConnectorDeliveryAttempt.created_at.desc())
                )
            ).scalars()
            for attempt in attempts:
                deliveries_by_request.setdefault(attempt.employee_request_id, attempt)
    views = []
    for row, platform, source_name in rows:
        source_label = None
        if platform:
            platform_label = CONNECTOR_PLATFORM_LABELS.get(platform, platform)
            source_label = f"{platform_label} · {source_name}" if source_name else platform_label
        views.append(
            {
                **_employee_view(row).model_dump(),
                "description": row.description,
                "hr_note": row.hr_note,
                "hr_case_id": row.hr_case_id,
                "hr_owner_id": row.hr_owner_id,
                "connector_source_label": source_label,
                "delivery": (
                    _delivery_view(deliveries_by_request[row.id]).model_dump()
                    if row.id in deliveries_by_request
                    else None
                ),
            }
        )
    return views


async def hr_list_assignees(tenant_id: str, manager_id: str, manager_role: str) -> list[dict]:
    """HRBPs inside the manager's authorised org scope — the assign pool (audit P1-7).

    A manager needs an in-product way to hand an employee request to an HRBP;
    until now ``hr_owner_id`` was only writable via SQL, so the hrbp queue was
    structurally always empty.
    """
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.user import User

    if manager_role != "hr_manager":
        return []
    visible_user_ids = await resolve_visible_user_ids(tenant_id, manager_id, manager_role)
    if not visible_user_ids:
        return []
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(User.id, User.name, User.email).where(
                    User.tenant_id == tenant_id,
                    User.id.in_(visible_user_ids),
                    User.role == "hrbp",
                )
            )
        ).all()
    return [{"user_id": r[0], "name": r[1], "email": r[2]} for r in rows]


async def _load_owned(tenant_id: str, user_id: str, request_id: str) -> EmployeeRequestView:
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (
                await db.execute(
                    select(EmployeeRequest).where(
                        EmployeeRequest.tenant_id == tenant_id,
                        EmployeeRequest.id == request_id,
                        EmployeeRequest.created_by == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            # Uniform denial — no disclosure of whether the request exists (spec §3.3)
            raise NotFoundError("Request", request_id)
    return _employee_view(row)
