"""Object ownership contracts for resumable HR work."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def test_async_tasks_and_weekly_reports_record_the_creator():
    """A work object without creator identity cannot be filtered safely."""
    from app.data.models.infra import AsyncTask
    from app.data.models.scenarios import WeeklyReport

    try:
        task = AsyncTask(tenant_id="tenant-a", type="interview_digest", status="pending", created_by="hr-a")
        report = WeeklyReport(
            tenant_id="tenant-a",
            period="2026-W35",
            summary="summary",
            progress_json="[]",
            risks_json="[]",
            plan_json="[]",
            data_sources_json="[]",
            created_by="hr-a",
        )
    except TypeError:
        pytest.fail("work rows must accept and retain a created_by owner")

    assert task.created_by == "hr-a"
    assert report.created_by == "hr-a"


def test_hrbp_visibility_scope_is_self_only_and_platform_roles_are_empty():
    """Role scope resolution must fail closed before any work query executes."""
    try:
        from app.access.object_scope import resolve_visible_user_ids
    except ImportError:
        pytest.fail("object-scope resolution is required before aggregation")

    assert asyncio.run(resolve_visible_user_ids("tenant-a", "hr-a", "hrbp")) == {"hr-a"}
    assert asyncio.run(resolve_visible_user_ids("tenant-a", "employee-a", "employee")) == set()
    assert asyncio.run(resolve_visible_user_ids("tenant-a", "admin-a", "admin")) == set()


def test_manager_scope_is_an_explicit_database_object():
    """Manager visibility must not be inferred from tenant membership or title."""
    from app.data.models.user import User

    try:
        from app.data.models.access_scope import ManagerOrgScope, OrgUnit
    except ImportError:
        pytest.fail("explicit organisation scope models are required for managers")

    org = OrgUnit(id="org-a", tenant_id="tenant-a", name="华东事业部")
    manager = User(
        id="manager-a",
        tenant_id="tenant-a",
        name="Manager A",
        email="manager-a@example.com",
        hashed_password="x",
        role="hr_manager",
        org_unit_id="org-a",
    )
    scope = ManagerOrgScope(
        tenant_id="tenant-a",
        manager_user_id="manager-a",
        org_unit_id="org-a",
    )

    assert org.id == "org-a"
    assert manager.org_unit_id == "org-a"
    assert scope.manager_user_id == "manager-a"
    assert scope.org_unit_id == "org-a"


@pytest.mark.asyncio
async def test_manager_sees_only_users_in_explicit_org_scopes():
    """Same-tenant membership alone must never grant manager visibility."""
    from sqlalchemy import delete

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.access_scope import ManagerOrgScope, OrgUnit
    from app.data.models.user import User

    tenant_id = str(uuid4())
    manager_id = str(uuid4())
    visible_user_id = str(uuid4())
    hidden_user_id = str(uuid4())
    org_a = str(uuid4())
    org_b = str(uuid4())
    factory = get_session_factory()

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all([OrgUnit(id=org_a, tenant_id=tenant_id, name="A"), OrgUnit(id=org_b, tenant_id=tenant_id, name="B")])
        await db.flush()
        db.add_all(
            [
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="M",
                    email=f"{manager_id}@example.com",
                    hashed_password="x",
                    role="hr_manager",
                    org_unit_id=org_a,
                ),
                User(
                    id=visible_user_id,
                    tenant_id=tenant_id,
                    name="A",
                    email=f"{visible_user_id}@example.com",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_a,
                ),
                User(
                    id=hidden_user_id,
                    tenant_id=tenant_id,
                    name="B",
                    email=f"{hidden_user_id}@example.com",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_b,
                ),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a))
        await db.commit()

    try:
        visible = await resolve_visible_user_ids(tenant_id, manager_id, "hr_manager")
        assert visible == {manager_id, visible_user_id}
        assert hidden_user_id not in visible
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_work_summary_filters_by_creator_before_aggregation():
    """A newer record owned by another HRBP must never enter this user's buckets."""
    from sqlalchemy import delete

    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask
    from app.data.models.scenarios import WeeklyReport
    from app.data.models.user import User
    from app.scenarios.work_summary.service import collect_work_summaries

    tenant_id = str(uuid4())
    actor_id = str(uuid4())
    other_id = str(uuid4())
    actor_task_id = str(uuid4())
    other_task_id = str(uuid4())
    actor_report_id = str(uuid4())
    factory = get_session_factory()
    now = datetime.now(UTC)

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=actor_id,
                    tenant_id=tenant_id,
                    name="A",
                    email=f"{actor_id}@example.com",
                    hashed_password="x",
                    role="hrbp",
                ),
                User(
                    id=other_id,
                    tenant_id=tenant_id,
                    name="B",
                    email=f"{other_id}@example.com",
                    hashed_password="x",
                    role="hrbp",
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                AsyncTask(
                    id=actor_task_id,
                    tenant_id=tenant_id,
                    type="interview_digest",
                    status="completed",
                    created_by=actor_id,
                    updated_at=now,
                ),
                AsyncTask(
                    id=other_task_id,
                    tenant_id=tenant_id,
                    type="voice_insight",
                    status="completed",
                    created_by=other_id,
                    updated_at=now + timedelta(seconds=5),
                ),
                WeeklyReport(
                    id=actor_report_id,
                    tenant_id=tenant_id,
                    created_by=actor_id,
                    period="2026-W35",
                    summary="s",
                    progress_json="[]",
                    risks_json="[]",
                    plan_json="[]",
                    data_sources_json="[]",
                    updated_at=now,
                ),
            ]
        )
        await db.commit()

    try:
        try:
            result = await collect_work_summaries(tenant_id, actor_id, "hrbp")
        except TypeError:
            pytest.fail("work aggregation must accept the actor role and filter object owners")
        visible_ids = {
            item.work_id
            for item in [result.continue_work, *result.attention, *result.completed_today]
            if item is not None
        }
        assert actor_task_id in visible_ids
        assert actor_report_id in visible_ids
        assert other_task_id not in visible_ids
    finally:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(AsyncTask).where(AsyncTask.tenant_id == tenant_id))
            await db.execute(delete(WeeklyReport).where(WeeklyReport.tenant_id == tenant_id))
            await db.execute(delete(User).where(User.tenant_id == tenant_id))
            await db.commit()
