"""Edge and authorization coverage for persistent work tasks.

Covers the boundaries the golden journeys do not: cross-scope owners,
invisible parents, unit-progress integrity, cancelled subtasks in the
parent denominator, real completion timestamps, and manager org scope.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.user import User
from app.data.models.work_task import WorkTask
from app.main import create_app


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(tenant_id: str, user_id: str, role: str = "hrbp") -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": user_id,
                "role": role,
                "tenant_id": tenant_id,
                "email": f"{user_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


async def _seed_tenant(tenant_id: str) -> dict[str, str]:
    """Seed an HRBP, a peer HRBP (no org), a manager, and two org units."""
    hrbp_id, peer_id, manager_id = (str(uuid4()) for _ in range(3))
    org_a_id, org_b_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                OrgUnit(id=org_a_id, tenant_id=tenant_id, name="华东团队"),
                OrgUnit(id=org_b_id, tenant_id=tenant_id, name="华南团队"),
                User(
                    id=hrbp_id,
                    tenant_id=tenant_id,
                    name="华东HRBP",
                    email=f"{hrbp_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                    org_unit_id=org_a_id,
                ),
                User(
                    id=peer_id,
                    tenant_id=tenant_id,
                    name="无组织HRBP",
                    email=f"{peer_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                    org_unit_id=None,
                ),
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="经理",
                    email=f"{manager_id}@example.test",
                    hashed_password="x",
                    role="hr_manager",
                    org_unit_id=org_a_id,
                ),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a_id))
        await db.commit()
    return {
        "hrbp": hrbp_id,
        "peer": peer_id,
        "manager": manager_id,
        "org_a": org_a_id,
        "org_b": org_b_id,
    }


async def _seed_extra_hrbp(tenant_id: str, org_unit_id: str) -> str:
    user_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="额外HRBP",
                email=f"{user_id}@example.test",
                hashed_password="x",
                role="hrbp",
                org_unit_id=org_unit_id,
            )
        )
        await db.commit()
    return user_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(WorkTask).where(WorkTask.tenant_id == tenant_id))
        await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
        await db.commit()


def _create(client: TestClient, headers: dict, **payload) -> dict:
    response = client.post("/api/work-summaries/tasks", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client: TestClient, headers: dict, task_id: str, payload: dict):
    return client.patch(f"/api/work-summaries/tasks/{task_id}", headers=headers, json=payload)


def test_cannot_assign_owner_outside_visible_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = asyncio.run(_seed_tenant(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        response = client.post(
            "/api/work-summaries/tasks",
            headers=headers,
            json={"title": "越权指派", "owner_user_id": str(uuid4())},
        )
        assert response.status_code == 404, response.text

        task = _create(client, headers, title="自有任务")
        cross = _patch(client, headers, task["task_id"], {"owner_user_id": str(uuid4())})
        assert cross.status_code == 404, cross.text
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_cannot_split_or_update_an_invisible_task(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = asyncio.run(_seed_tenant(tenant_id))
    owner_headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    peer_headers = {"Authorization": f"Bearer {_token(tenant_id, ids['peer'])}"}
    try:
        task = _create(client, owner_headers, title="他人的任务")

        split = client.post(
            f"/api/work-summaries/tasks/{task['task_id']}/subtasks",
            headers=peer_headers,
            json={"title": "偷拆子任务"},
        )
        assert split.status_code == 404, split.text

        patched = _patch(client, peer_headers, task["task_id"], {"status": "completed"})
        assert patched.status_code == 404, patched.text
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_unit_progress_integrity_boundaries(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = asyncio.run(_seed_tenant(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        task = _create(client, headers, title="数量任务", total_units=3)

        over = _patch(client, headers, task["task_id"], {"completed_units": 4})
        assert over.status_code == 400, over.text

        shrink = _patch(client, headers, task["task_id"], {"total_units": 2, "completed_units": 0})
        assert shrink.status_code == 200, shrink.text
        over_after_shrink = _patch(client, headers, task["task_id"], {"completed_units": 3})
        assert over_after_shrink.status_code == 400, over_after_shrink.text

        stage_task = _create(client, headers, title="阶段任务")
        forged = _patch(client, headers, stage_task["task_id"], {"completed_units": 2})
        assert forged.status_code == 400, forged.text

        bad_total = client.post(
            "/api/work-summaries/tasks",
            headers=headers,
            json={"title": "零总量", "total_units": 0},
        )
        assert bad_total.status_code == 422, bad_total.text
    finally:
        asyncio.run(_cleanup(tenant_id))


async def test_cancelled_subtask_leaves_the_parent_denominator_and_reopens_recount(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        parent = _create(client, headers, title="父任务")
        sub_a = client.post(
            f"/api/work-summaries/tasks/{parent['task_id']}/subtasks",
            headers=headers,
            json={"title": "子任务A"},
        ).json()
        sub_b = client.post(
            f"/api/work-summaries/tasks/{parent['task_id']}/subtasks",
            headers=headers,
            json={"title": "子任务B"},
        ).json()

        done_a = _patch(client, headers, sub_a["task_id"], {"status": "completed"})
        assert done_a.status_code == 200, done_a.text

        cancelled_b = _patch(client, headers, sub_b["task_id"], {"status": "cancelled"})
        assert cancelled_b.status_code == 200, cancelled_b.text

        parent_row = await _load_task(tenant_id, parent["task_id"])
        assert parent_row["total_units"] == 1, "cancelled subtask must leave the denominator"
        assert parent_row["completed_units"] == 1

        reopened = _patch(client, headers, sub_b["task_id"], {"status": "open"})
        assert reopened.status_code == 200, reopened.text
        parent_row = await _load_task(tenant_id, parent["task_id"])
        assert parent_row["total_units"] == 2
        assert parent_row["completed_units"] == 1
    finally:
        await _cleanup(tenant_id)


async def _load_task(tenant_id: str, task_id: str) -> dict:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.get(WorkTask, task_id)
        return {
            "total_units": row.total_units,
            "completed_units": row.completed_units,
            "completed_at": row.completed_at,
        }


async def test_completion_uses_a_real_completed_at_not_any_updated_at(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        task = _create(client, headers, title="完成时间任务")
        before = datetime.now(UTC)

        done = _patch(client, headers, task["task_id"], {"status": "completed"})
        assert done.status_code == 200, done.text
        assert done.json()["completed_at"] is not None

        row = await _load_task(tenant_id, task["task_id"])
        completed_at = row["completed_at"]
        assert completed_at is not None
        assert completed_at.tzinfo is not None
        assert completed_at >= before.replace(tzinfo=completed_at.tzinfo) - timedelta(seconds=5)

        reopened = _patch(client, headers, task["task_id"], {"status": "open"})
        assert reopened.json()["completed_at"] is None, "reopening must clear completed_at"
    finally:
        await _cleanup(tenant_id)


def test_manager_scope_governs_task_mutations(client: TestClient) -> None:
    tenant_id = str(uuid4())
    ids = asyncio.run(_seed_tenant(tenant_id))
    outside_hrbp = asyncio.run(_seed_extra_hrbp(tenant_id, ids["org_b"]))
    manager_headers = {"Authorization": f"Bearer {_token(tenant_id, ids['manager'], 'hr_manager')}"}
    try:
        inside = _create(
            client,
            {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"},
            title="范围内任务",
        )
        outside = _create(
            client,
            {"Authorization": f"Bearer {_token(tenant_id, outside_hrbp)}"},
            title="范围外任务",
        )

        summary = client.get("/api/work-summaries", headers=manager_headers)
        assert summary.status_code == 200, summary.text
        payload = summary.json()
        visible_ids = set()
        for bucket in ("attention", "completed_today"):
            for item in payload.get(bucket) or []:
                visible_ids.add(item["work_id"])
        if payload.get("continue_work"):
            visible_ids.add(payload["continue_work"]["work_id"])
        assert inside["task_id"] in visible_ids
        assert outside["task_id"] not in visible_ids

        blocked = _patch(manager_headers and client, manager_headers, outside["task_id"], {"status": "completed"})
        assert blocked.status_code == 404, blocked.text

        allowed = _patch(client, manager_headers, inside["task_id"], {"status": "in_progress"})
        assert allowed.status_code == 200, allowed.text
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_cross_tenant_access_fails_closed(client: TestClient) -> None:
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    ids_a = asyncio.run(_seed_tenant(tenant_a))
    ids_b = asyncio.run(_seed_tenant(tenant_b))
    try:
        task = _create(
            client,
            {"Authorization": f"Bearer {_token(tenant_a, ids_a['hrbp'])}"},
            title="租户A任务",
        )

        foreign = _patch(
            client,
            {"Authorization": f"Bearer {_token(tenant_b, ids_b['hrbp'])}"},
            task["task_id"],
            {"status": "completed"},
        )
        assert foreign.status_code == 404, foreign.text

        foreign_split = client.post(
            f"/api/work-summaries/tasks/{task['task_id']}/subtasks",
            headers={"Authorization": f"Bearer {_token(tenant_b, ids_b['hrbp'])}"},
            json={"title": "跨租户子任务"},
        )
        assert foreign_split.status_code == 404, foreign_split.text
    finally:
        asyncio.run(_cleanup(tenant_a))
        asyncio.run(_cleanup(tenant_b))


@pytest.mark.asyncio
async def _real_http_patch(tenant_id: str, token: str, task_id: str, payload: dict) -> int:
    """Issue a REAL concurrent PATCH over the ASGI transport (not a serial TestClient)."""
    import httpx

    from app.main import create_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as http:
        response = await http.patch(
            f"/api/work-summaries/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        return response.status_code


@pytest.mark.asyncio
async def test_concurrent_subtask_completion_aggregates_to_the_parent(client: TestClient) -> None:
    """Two subtasks completing in parallel must leave the parent at 2/2.

    Regression for the lost-update race found by independent review: the old
    test used a synchronous TestClient inside asyncio.gather — the requests
    were serial, and every writer sent the same value, so the aggregation race
    was never exercised. Real parallel HTTP (ASGITransport) over two different
    subtasks reproduces "both done, parent stuck at 1/2".
    """
    import asyncio as _asyncio
    import os

    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        parent = client.post(
            "/api/work-summaries/tasks",
            headers=headers,
            json={"title": "并发父任务"},
        )
        assert parent.status_code == 201, parent.text
        parent_id = parent.json()["task_id"]

        sub_ids = []
        for name in ("子任务甲", "子任务乙"):
            sub = client.post(
                f"/api/work-summaries/tasks/{parent_id}/subtasks",
                headers=headers,
                json={"title": name},
            )
            assert sub.status_code == 201, sub.text
            sub_ids.append(sub.json()["task_id"])

        token = _token(tenant_id, ids["hrbp"])
        barrier = _asyncio.Barrier(2)

        async def complete(sub_id: str) -> int:
            await barrier.wait()
            return await _real_http_patch(tenant_id, token, sub_id, {"status": "completed"})

        codes = await _asyncio.gather(complete(sub_ids[0]), complete(sub_ids[1]))
        assert codes == [200, 200], codes

        row = await _load_task(tenant_id, parent_id)
        assert row["total_units"] == 2, "denominator must count both subtasks"
        assert row["completed_units"] == 2, (
            f"lost update: both subtasks completed but parent shows {row['completed_units']}/2"
        )
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_concurrent_completion_cannot_exceed_total_units(client: TestClient) -> None:
    """Racing PATCHes on one task must never push completed_units past total."""
    import asyncio as _asyncio
    import os

    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    headers = {"Authorization": f"Bearer {_token(tenant_id, ids['hrbp'])}"}
    try:
        created = client.post(
            "/api/work-summaries/tasks",
            headers=headers,
            json={"title": "并发完成", "total_units": 3},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        token = _token(tenant_id, ids["hrbp"])
        barrier = _asyncio.Barrier(4)

        async def advance() -> int:
            await barrier.wait()
            return await _real_http_patch(
                tenant_id,
                token,
                task_id,
                {"completed_units": 2},
            )

        codes = await _asyncio.gather(*(advance() for _ in range(4)))
        # Every writer asks for the same legal value — none may corrupt the row.
        assert all(code in (200, 400, 404) for code in codes), codes

        row = await _load_task(tenant_id, task_id)
        assert row["completed_units"] <= row["total_units"]
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_concurrent_advance_increments_exactly_once_per_click() -> None:
    """TASK-02: N concurrent advance clicks on a units task must result in
    exactly N increments — the server-side atomic UPDATE prevents two clients
    reading the same completed_units and both writing N+1."""
    import asyncio as _asyncio
    import os

    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    token = _token(tenant_id, ids["hrbp"])
    import httpx

    from app.main import create_app

    try:
        client = TestClient(create_app())
        created = client.post(
            "/api/work-summaries/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "原子推进", "total_units": 10},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        barrier = _asyncio.Barrier(5)

        async def advance() -> int:
            await barrier.wait()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()),
                base_url="http://test",
            ) as http:
                response = await http.post(
                    f"/api/work-summaries/tasks/{task_id}/advance",
                    headers={"Authorization": f"Bearer {token}"},
                )
                return response.status_code

        codes = await _asyncio.gather(*(advance() for _ in range(5)))
        assert codes.count(200) == 5, f"expected 5 successful advances, got {codes}"

        row = await _load_task(tenant_id, task_id)
        assert row["completed_units"] == 5, f"expected exactly 5 increments, got {row}"
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_database_check_constraint_blocks_illegal_units() -> None:
    """The truthful-progress CHECK must reject bad rows even from raw SQL."""
    import os

    from sqlalchemy import text

    from app.data.database import get_engine

    if not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS"):
        pytest.skip("set HRBP_RUN_DB_SECURITY_TESTS=true for PostgreSQL verification")

    tenant_id = str(uuid4())
    ids = await _seed_tenant(tenant_id)
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": tenant_id},
            )
            # completed_units > total_units must violate ck_work_tasks_truthful_progress.
            from sqlalchemy import text as sa_text

            try:
                await conn.execute(
                    sa_text(
                        "INSERT INTO work_tasks (id, tenant_id, created_by, owner_user_id, title, next_action, "
                        "status, progress_mode, completed_units, total_units) "
                        "VALUES (:id, :tenant, :creator, :owner, '非法进度', 'x', 'open', 'units', 5, 3)"
                    ),
                    {"id": str(uuid4()), "tenant": tenant_id, "creator": ids["hrbp"], "owner": ids["hrbp"]},
                )
                raise AssertionError("database accepted completed_units > total_units")
            except AssertionError:
                raise
            except Exception as exc:
                assert "ck_work_tasks_truthful_progress" in str(exc), f"unexpected error: {exc}"
    finally:
        await _cleanup(tenant_id)


def test_assignable_owners_endpoint_exposes_only_visible_scope(client: TestClient) -> None:
    """Managers pick assignees from their visible scope, not the admin user list."""
    tenant_id = str(uuid4())
    ids = asyncio.run(_seed_tenant(tenant_id))
    manager_headers = {"Authorization": f"Bearer {_token(tenant_id, ids['manager'], 'hr_manager')}"}
    peer_headers = {"Authorization": f"Bearer {_token(tenant_id, ids['peer'])}"}
    try:
        response = client.get("/api/work-summaries/assignable-owners", headers=manager_headers)

        assert response.status_code == 200, response.text
        payload = response.json()
        returned_ids = {entry["user_id"] for entry in payload["owners"]}
        assert ids["hrbp"] in returned_ids, "in-scope HRBP must be assignable"
        assert ids["peer"] not in returned_ids, "unscoped peer must stay invisible"
        assert all(set(entry) == {"user_id", "name"} for entry in payload["owners"])

        # HRBP sees only themselves in the assignable list.
        peer_list = client.get("/api/work-summaries/assignable-owners", headers=peer_headers)
        assert peer_list.status_code == 200, peer_list.text
        assert {entry["user_id"] for entry in peer_list.json()["owners"]} == {ids["peer"]}
    finally:
        asyncio.run(_cleanup(tenant_id))


@pytest.mark.asyncio
async def test_cross_tenant_parent_binding_is_rejected_by_the_database() -> None:
    """A child row pointing at another tenant's parent must fail at the DB level.

    The single-column self FK only checks id existence, so without a composite
    (tenant_id, parent_task_id) constraint any future writer that bypasses the
    service layer could bind tasks across tenants (review P0 finding).
    """
    import os

    if not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS"):
        pytest.skip("set HRBP_RUN_DB_SECURITY_TESTS=true for PostgreSQL verification")

    from sqlalchemy import text as sa_text

    from app.data.database import get_engine

    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    ids = await _seed_tenant(tenant_a)
    ids_b = await _seed_tenant(tenant_b)
    engine = get_engine()
    child_id = str(uuid4())
    try:
        async with engine.begin() as conn:
            await conn.execute(sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_a})
            # seed a parent in tenant A
            parent_id = str(uuid4())
            await conn.execute(
                sa_text(
                    "INSERT INTO work_tasks (id, tenant_id, created_by, owner_user_id, title, next_action, status, progress_mode, completed_units) "
                    "VALUES (:id, :t, :c, :o, '父任务', '', 'open', 'stage', 0)"
                ),
                {"id": parent_id, "t": tenant_a, "c": ids["hrbp"], "o": ids["hrbp"]},
            )
        # Switch to tenant B context and try to bind a child to tenant A's parent.
        async with engine.begin() as conn:
            await conn.execute(sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
            try:
                await conn.execute(
                    sa_text(
                        "INSERT INTO work_tasks (id, tenant_id, created_by, owner_user_id, parent_task_id, title, next_action, status, progress_mode, completed_units) "
                        "VALUES (:id, :t, :c, :o, :p, '跨租户子任务', '', 'open', 'stage', 0)"
                    ),
                    {"id": child_id, "t": tenant_b, "c": ids_b["hrbp"], "o": ids_b["hrbp"], "p": parent_id},
                )
                raise AssertionError("database accepted a cross-tenant parent binding — composite FK missing")
            except AssertionError:
                raise
            except Exception as exc:
                # Expected: FK/unique violation or RLS — anything that rejects is fine.
                assert any(token in str(exc) for token in ("fk_work_tasks_tenant_parent", "foreign key", "violates")), (
                    f"unexpected rejection reason: {exc}"
                )
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_a})
            await conn.execute(sa_text("DELETE FROM work_tasks WHERE tenant_id = :t"), {"t": tenant_a})
            await conn.execute(sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
            await conn.execute(sa_text("DELETE FROM work_tasks WHERE tenant_id = :t"), {"t": tenant_b})
        await _cleanup(tenant_a)
        await _cleanup(tenant_b)
