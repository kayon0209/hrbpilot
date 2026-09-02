"""Admin organisation configuration must be durable and audited."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.infra import AuditLog
from app.data.models.user import User
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(tenant_id: str, admin_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": admin_id,
                "role": "admin",
                "tenant_id": tenant_id,
                "email": f"{admin_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": "hrbp-ai-workbench",
                "aud": "hrbp-ai-workbench",
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


async def _seed_users(tenant_id: str) -> tuple[str, str, str]:
    admin_id, manager_id, employee_id = (str(uuid4()) for _ in range(3))
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=admin_id,
                    tenant_id=tenant_id,
                    name="Admin",
                    email=f"{admin_id}@example.test",
                    hashed_password="x",
                    role="admin",
                ),
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="Manager",
                    email=f"{manager_id}@example.test",
                    hashed_password="x",
                    role="hr_manager",
                ),
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="x",
                    role="employee",
                ),
            ]
        )
        await db.commit()
    return admin_id, manager_id, employee_id


async def _seed_org(tenant_id: str, name: str = "华东事业部") -> str:
    org_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(OrgUnit(id=org_id, tenant_id=tenant_id, name=name))
        await db.commit()
    return org_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
        await db.commit()


def test_admin_creates_an_audited_org_unit(client) -> None:
    tenant_id = str(uuid4())
    admin_id, _, _ = asyncio.run(_seed_users(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        response = client.post(
            "/api/admin/users/org-units",
            headers=headers,
            json={"name": "华东事业部"},
        )

        assert response.status_code == 200, response.text
        org_id = response.json()["org_unit_id"]

        async def load() -> tuple[str | None, list[str]]:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                name = await db.scalar(
                    select(OrgUnit.name).where(OrgUnit.tenant_id == tenant_id, OrgUnit.id == org_id)
                )
                actions = list(
                    (
                        await db.execute(
                            select(AuditLog.scenario_id).where(
                                AuditLog.tenant_id == tenant_id,
                                AuditLog.user_id == admin_id,
                            )
                        )
                    ).scalars()
                )
            return name, actions

        name, actions = asyncio.run(load())
        assert name == "华东事业部"
        assert actions == ["org_unit.created"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_inventory_lists_org_units(client) -> None:
    tenant_id = str(uuid4())
    admin_id, _, _ = asyncio.run(_seed_users(tenant_id))
    org_id = asyncio.run(_seed_org(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        response = client.get("/api/admin/users", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["org_units"] == [
            {"org_unit_id": org_id, "name": "华东事业部", "parent_id": None}
        ]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_assigns_a_user_to_an_org_unit(client) -> None:
    tenant_id = str(uuid4())
    admin_id, _, employee_id = asyncio.run(_seed_users(tenant_id))
    org_id = asyncio.run(_seed_org(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        response = client.put(
            f"/api/admin/users/{employee_id}/org-unit",
            headers=headers,
            json={"org_unit_id": org_id},
        )

        assert response.status_code == 200, response.text
        assert response.json()["org_unit_id"] == org_id

        async def load() -> tuple[str | None, list[str]]:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                assigned = await db.scalar(
                    select(User.org_unit_id).where(User.tenant_id == tenant_id, User.id == employee_id)
                )
                actions = list(
                    (
                        await db.execute(
                            select(AuditLog.scenario_id).where(
                                AuditLog.tenant_id == tenant_id,
                                AuditLog.user_id == admin_id,
                            )
                        )
                    ).scalars()
                )
            return assigned, actions

        assigned, actions = asyncio.run(load())
        assert assigned == org_id
        assert actions == ["user.org_unit_assigned"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_replaces_a_managers_audited_org_scope(client) -> None:
    tenant_id = str(uuid4())
    admin_id, manager_id, _ = asyncio.run(_seed_users(tenant_id))
    org_a = asyncio.run(_seed_org(tenant_id, "华东事业部"))
    org_b = asyncio.run(_seed_org(tenant_id, "华南事业部"))
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        response = client.put(
            f"/api/admin/users/{manager_id}/manager-scopes",
            headers=headers,
            json={"org_unit_ids": [org_a, org_b]},
        )

        assert response.status_code == 200, response.text
        assert set(response.json()["org_unit_ids"]) == {org_a, org_b}

        inventory = client.get("/api/admin/users", headers=headers)
        manager = next(item for item in inventory.json()["users"] if item["user_id"] == manager_id)
        assert set(manager["manager_scope_org_unit_ids"]) == {org_a, org_b}

        async def load() -> tuple[set[str], list[str]]:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                scopes = set(
                    (
                        await db.execute(
                            select(ManagerOrgScope.org_unit_id).where(
                                ManagerOrgScope.tenant_id == tenant_id,
                                ManagerOrgScope.manager_user_id == manager_id,
                            )
                        )
                    ).scalars()
                )
                actions = list(
                    (
                        await db.execute(
                            select(AuditLog.scenario_id).where(
                                AuditLog.tenant_id == tenant_id,
                                AuditLog.user_id == admin_id,
                            )
                        )
                    ).scalars()
                )
            return scopes, actions

        scopes, actions = asyncio.run(load())
        assert scopes == {org_a, org_b}
        assert actions == ["manager_org_scope.replaced"]
    finally:
        asyncio.run(_cleanup(tenant_id))
