"""Admin-only tenant user and organisation-scope configuration."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_session_factory
from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.infra import AsyncTask
from app.data.models.scenarios import CultureContent, KnowledgeFeedbackCandidate, WeeklyReport
from app.data.models.user import User
from app.shared.audit import append_security_audit_event
from app.shared.errors import AppError, NotFoundError

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class CreateOrgUnitBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: str | None = Field(None, max_length=36)


class AssignOrgUnitBody(BaseModel):
    org_unit_id: str | None = Field(None, max_length=36)


class ReplaceManagerScopesBody(BaseModel):
    org_unit_ids: list[str] = Field(default_factory=list, max_length=100)


class ClaimLegacyWorkBody(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=36)


@router.get("")
@require_auth
@require_capability("user_admin")
async def list_users(request: Request):
    """Return the current tenant's user-role assignments without business data."""
    tenant_id = require_tenant_id(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(User, OrgUnit.name)
                .outerjoin(OrgUnit, OrgUnit.id == User.org_unit_id)
                .where(User.tenant_id == tenant_id)
                .order_by(User.name, User.email)
            )
        ).all()

        scope_rows = (
            await db.execute(
                select(ManagerOrgScope.manager_user_id, ManagerOrgScope.org_unit_id).where(
                    ManagerOrgScope.tenant_id == tenant_id
                )
            )
        ).all()
        org_rows = (
            (
                await db.execute(
                    select(OrgUnit)
                    .where(OrgUnit.tenant_id == tenant_id)
                    .order_by(OrgUnit.name, OrgUnit.id)
                )
            )
            .scalars()
            .all()
        )

    scopes_by_manager: dict[str, list[str]] = {}
    for manager_user_id, org_unit_id in scope_rows:
        scopes_by_manager.setdefault(manager_user_id, []).append(org_unit_id)

    return {
        "users": [
            {
                "user_id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "org_unit_id": user.org_unit_id,
                "org_unit": org_name,
                "manager_scope_org_unit_ids": scopes_by_manager.get(user.id, []),
            }
            for user, org_name in rows
        ],
        "org_units": [
            {"org_unit_id": org.id, "name": org.name, "parent_id": org.parent_id}
            for org in org_rows
        ],
    }


@router.get("/legacy-work")
@require_auth
@require_capability("user_admin")
async def list_legacy_work(request: Request):
    """Report ownerless migrated work without guessing who should own it."""
    tenant_id = require_tenant_id(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        tasks = list(
            (
                await db.execute(
                    select(AsyncTask).where(
                        AsyncTask.tenant_id == tenant_id,
                        AsyncTask.created_by.is_(None),
                    )
                )
            ).scalars()
        )
        reports = list(
            (
                await db.execute(
                    select(WeeklyReport).where(
                        WeeklyReport.tenant_id == tenant_id,
                        WeeklyReport.created_by.is_(None),
                    )
                )
            ).scalars()
        )
        candidates = list(
            (
                await db.execute(
                    select(KnowledgeFeedbackCandidate).where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.org_unit_id.is_(None),
                        KnowledgeFeedbackCandidate.source_user_id.is_(None),
                    )
                )
            ).scalars()
        )
        drafts = list(
            (
                await db.execute(
                    select(CultureContent).where(
                        CultureContent.tenant_id == tenant_id,
                        CultureContent.created_by.is_(None),
                    )
                )
            ).scalars()
        )

    items = [
        {
            "work_id": task.id,
            "work_type": "async_task",
            "title": "面谈纪要分析" if task.type == "interview_digest" else "员工声音分析",
        }
        for task in tasks
    ]
    items.extend(
        {
            "work_id": report.id,
            "work_type": "weekly_report",
            "title": f"周报 {report.period}",
        }
        for report in reports
    )
    items.extend(
        {
            "work_id": candidate.id,
            "work_type": "knowledge_feedback_candidate",
            "title": f"知识反馈候选：{candidate.question[:80]}",
        }
        for candidate in candidates
    )
    items.extend(
        {
            "work_id": draft.id,
            "work_type": "culture_content",
            "title": f"无归属文化草稿：{draft.news_article[:80]}",
        }
        for draft in drafts
    )
    return {"items": items, "total": len(items)}


@router.put("/legacy-work/{work_type}/{work_id}/owner")
@require_auth
@require_capability("user_admin")
async def claim_legacy_work(
    work_type: str,
    work_id: str,
    body: ClaimLegacyWorkBody,
    request: Request,
):
    """Assign one ownerless migrated object after an administrator verifies ownership."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        owner = await db.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                User.id == body.user_id,
            )
        )
        if owner is None:
            raise NotFoundError("User", body.user_id)
        if owner.role not in {"hrbp", "hr_manager"}:
            raise AppError(
                "历史 HR 工作只能认领给 HRBP 或 HR 经理",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        legacy_row: AsyncTask | WeeklyReport | CultureContent | KnowledgeFeedbackCandidate | None
        if work_type == "async_task":
            legacy_row = await db.scalar(
                select(AsyncTask).where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.id == work_id,
                )
            )
        elif work_type == "weekly_report":
            legacy_row = await db.scalar(
                select(WeeklyReport).where(
                    WeeklyReport.tenant_id == tenant_id,
                    WeeklyReport.id == work_id,
                )
            )
        elif work_type == "culture_content":
            legacy_row = await db.scalar(
                select(CultureContent).where(
                    CultureContent.tenant_id == tenant_id,
                    CultureContent.id == work_id,
                )
            )
        elif work_type == "knowledge_feedback_candidate":
            legacy_row = await db.scalar(
                select(KnowledgeFeedbackCandidate).where(
                    KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                    KnowledgeFeedbackCandidate.id == work_id,
                )
            )
        else:
            raise AppError("不支持的历史工作类型", code="VALIDATION_ERROR", status_code=400)

        if legacy_row is None:
            raise NotFoundError("Legacy work", work_id)

        # CULT-01: the claim is a guarded UPDATE ... WHERE owner IS NULL so two
        # concurrent admins claiming the same ownerless row cannot both win.
        from typing import Any, cast

        from sqlalchemy import update
        from sqlalchemy.engine import CursorResult

        if isinstance(legacy_row, KnowledgeFeedbackCandidate):
            claim = cast(
                CursorResult[Any],
                await db.execute(
                    update(KnowledgeFeedbackCandidate)
                    .where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.id == work_id,
                        KnowledgeFeedbackCandidate.source_user_id.is_(None),
                    )
                    .values(source_user_id=owner.id, updated_at=datetime.now(UTC))
                ),
            )
        else:
            claim = cast(
                CursorResult[Any],
                await db.execute(
                    update(type(legacy_row))
                    .where(
                        type(legacy_row).tenant_id == tenant_id,
                        type(legacy_row).id == work_id,
                        type(legacy_row).created_by.is_(None),
                    )
                    .values(created_by=owner.id, updated_at=datetime.now(UTC))
                ),
            )
        if claim.rowcount != 1:
            await db.rollback()
            raise AppError("该历史工作已有负责人，已被他人认领", code="CONFLICT", status_code=409)

        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="legacy_work.claimed",
            object_type=work_type,
            object_id=work_id,
            details={"owner_user_id": owner.id},
        )
        await db.commit()

    return {"work_id": work_id, "work_type": work_type, "owner_user_id": owner.id}


@router.post("/org-units")
@require_auth
@require_capability("user_admin")
async def create_org_unit(body: CreateOrgUnitBody, request: Request):
    """Create a tenant-scoped organisation unit and audit the mutation."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        if body.parent_id:
            parent = await db.scalar(
                select(OrgUnit).where(
                    OrgUnit.tenant_id == tenant_id,
                    OrgUnit.id == body.parent_id,
                )
            )
            if parent is None:
                raise NotFoundError("Org unit", body.parent_id)

        name = body.name.strip()
        if not name:
            raise AppError("组织名称不能为空", code="VALIDATION_ERROR", status_code=400)
        row = OrgUnit(tenant_id=tenant_id, name=name, parent_id=body.parent_id)
        db.add(row)
        await db.flush()
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="org_unit.created",
            object_type="org_unit",
            object_id=row.id,
            details={"name": name, "parent_id": body.parent_id},
        )
        await db.commit()

    return {"org_unit_id": row.id, "name": row.name, "parent_id": row.parent_id}


@router.put("/{user_id}/org-unit")
@require_auth
@require_capability("user_admin")
async def assign_org_unit(user_id: str, body: AssignOrgUnitBody, request: Request):
    """Assign or clear one user's organisation membership."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        user = await db.scalar(select(User).where(User.tenant_id == tenant_id, User.id == user_id))
        if user is None:
            raise NotFoundError("User", user_id)

        org_name: str | None = None
        if body.org_unit_id:
            org = await db.scalar(
                select(OrgUnit).where(
                    OrgUnit.tenant_id == tenant_id,
                    OrgUnit.id == body.org_unit_id,
                )
            )
            if org is None:
                raise NotFoundError("Org unit", body.org_unit_id)
            org_name = org.name

        previous_org_unit_id = user.org_unit_id
        user.org_unit_id = body.org_unit_id
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="user.org_unit_assigned",
            object_type="user",
            object_id=user.id,
            details={
                "previous_org_unit_id": previous_org_unit_id,
                "org_unit_id": body.org_unit_id,
            },
        )
        await db.commit()

    return {"user_id": user.id, "org_unit_id": body.org_unit_id, "org_unit": org_name}


@router.put("/{manager_id}/manager-scopes")
@require_auth
@require_capability("user_admin")
async def replace_manager_scopes(manager_id: str, body: ReplaceManagerScopesBody, request: Request):
    """Atomically replace the explicit organisation scope for one manager."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    requested_ids = list(dict.fromkeys(body.org_unit_ids))
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        manager = await db.scalar(
            select(User).where(User.tenant_id == tenant_id, User.id == manager_id)
        )
        if manager is None:
            raise NotFoundError("User", manager_id)
        if manager.role != "hr_manager":
            raise AppError("只有 HR 经理可以配置组织授权范围", code="VALIDATION_ERROR", status_code=400)

        existing_ids = list(
            (
                await db.execute(
                    select(ManagerOrgScope.org_unit_id).where(
                        ManagerOrgScope.tenant_id == tenant_id,
                        ManagerOrgScope.manager_user_id == manager_id,
                    )
                )
            ).scalars()
        )
        if requested_ids:
            valid_ids = set(
                (
                    await db.execute(
                        select(OrgUnit.id).where(
                            OrgUnit.tenant_id == tenant_id,
                            OrgUnit.id.in_(requested_ids),
                        )
                    )
                ).scalars()
            )
            missing_ids = [org_id for org_id in requested_ids if org_id not in valid_ids]
            if missing_ids:
                raise NotFoundError("Org unit", missing_ids[0])

        await db.execute(
            delete(ManagerOrgScope).where(
                ManagerOrgScope.tenant_id == tenant_id,
                ManagerOrgScope.manager_user_id == manager_id,
            )
        )
        db.add_all(
            [
                ManagerOrgScope(
                    tenant_id=tenant_id,
                    manager_user_id=manager_id,
                    org_unit_id=org_id,
                )
                for org_id in requested_ids
            ]
        )
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="manager_org_scope.replaced",
            object_type="user",
            object_id=manager_id,
            details={"previous_org_unit_ids": existing_ids, "org_unit_ids": requested_ids},
        )
        await db.commit()

    return {"manager_id": manager_id, "org_unit_ids": requested_ids}
