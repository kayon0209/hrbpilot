"""Culture-content reads must obey creator and manager organisation scope."""

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
from app.data.models.scenarios import CultureContent
from app.data.models.user import User
from app.main import create_app

pytestmark = pytest.mark.integration


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


async def _seed_content_and_users(tenant_id: str) -> tuple[str, str]:
    owner_id, other_user_id, content_id = (str(uuid4()) for _ in range(3))
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    name="Content owner",
                    email=f"{owner_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                ),
                User(
                    id=other_user_id,
                    tenant_id=tenant_id,
                    name="Other HRBP",
                    email=f"{other_user_id}@example.test",
                    hashed_password="x",
                    role="hrbp",
                ),
            ]
        )
        await db.flush()
        db.add(
            CultureContent(
                id=content_id,
                tenant_id=tenant_id,
                created_by=owner_id,
                keywords_json='["内部敏感主题"]',
                news_article="仅内容创建者可读",
                group_notice="notice",
                employee_story="story",
                event_copy="event",
                tone="formal",
            )
        )
        await db.commit()
    return other_user_id, content_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(CultureContent).where(CultureContent.tenant_id == tenant_id))
        await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
        await db.commit()


def test_hrbp_cannot_read_another_users_content_or_history(client: TestClient) -> None:
    tenant_id = str(uuid4())
    other_user_id, content_id = asyncio.run(_seed_content_and_users(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, other_user_id)}"}
    try:
        detail = client.get(f"/api/culture-content/{content_id}", headers=headers)
        history = client.get("/api/culture-content/history", headers=headers)

        assert detail.status_code == 404, detail.text
        assert history.status_code == 200, history.text
        assert history.json()["contents"] == []
    finally:
        asyncio.run(_cleanup(tenant_id))


async def _seed_manager_scope(tenant_id: str) -> tuple[str, str, str]:
    manager_id, owner_a_id, owner_b_id = (str(uuid4()) for _ in range(3))
    org_a_id, org_b_id = str(uuid4()), str(uuid4())
    visible_content_id, hidden_content_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                OrgUnit(id=org_a_id, tenant_id=tenant_id, name="East"),
                OrgUnit(id=org_b_id, tenant_id=tenant_id, name="South"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                User(id=manager_id, tenant_id=tenant_id, name="Manager", email=f"{manager_id}@example.test", hashed_password="x", role="hr_manager", org_unit_id=org_a_id),
                User(id=owner_a_id, tenant_id=tenant_id, name="East HRBP", email=f"{owner_a_id}@example.test", hashed_password="x", role="hrbp", org_unit_id=org_a_id),
                User(id=owner_b_id, tenant_id=tenant_id, name="South HRBP", email=f"{owner_b_id}@example.test", hashed_password="x", role="hrbp", org_unit_id=org_b_id),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a_id))
        for content_id, owner_id, article in (
            (visible_content_id, owner_a_id, "East article"),
            (hidden_content_id, owner_b_id, "South article"),
        ):
            db.add(
                CultureContent(
                    id=content_id,
                    tenant_id=tenant_id,
                    created_by=owner_id,
                    keywords_json="[]",
                    news_article=article,
                    group_notice="notice",
                    employee_story="story",
                    event_copy="event",
                    tone="formal",
                )
            )
        await db.commit()
    return manager_id, visible_content_id, hidden_content_id


def test_manager_reads_only_culture_content_in_explicit_org_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    manager_id, visible_content_id, hidden_content_id = asyncio.run(_seed_manager_scope(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, manager_id, 'hr_manager')}"}
    try:
        history = client.get("/api/culture-content/history", headers=headers)
        visible = client.get(f"/api/culture-content/{visible_content_id}", headers=headers)
        hidden = client.get(f"/api/culture-content/{hidden_content_id}", headers=headers)

        assert history.status_code == 200, history.text
        assert {item["content_id"] for item in history.json()["contents"]} == {visible_content_id}
        assert visible.status_code == 200, visible.text
        assert hidden.status_code == 404, hidden.text
    finally:
        asyncio.run(_cleanup(tenant_id))


async def _seed_legacy_ownerless_content(tenant_id: str) -> tuple[str, str]:
    """Create a creator-owned row plus a pre-migration row with NULL creator."""
    owner_id, legacy_content_id = str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=owner_id,
                tenant_id=tenant_id,
                name="Legacy probe owner",
                email=f"{owner_id}@example.test",
                hashed_password="x",
                role="hrbp",
            )
        )
        await db.flush()
        db.add(
            CultureContent(
                tenant_id=tenant_id,
                created_by=None,
                keywords_json="[]",
                news_article="legacy ownerless article",
                group_notice="notice",
                employee_story="story",
                event_copy="event",
                tone="formal",
            )
        )
        await db.commit()
    return owner_id, legacy_content_id


def test_legacy_ownerless_content_is_quarantined_from_detail_and_history(client: TestClient) -> None:
    """Rows migrated before created_by existed must fail closed everywhere."""
    tenant_id = str(uuid4())
    owner_id, _legacy_id = asyncio.run(_seed_legacy_ownerless_content(tenant_id))
    headers = {"Authorization": f"Bearer {_token(tenant_id, owner_id)}"}
    try:
        history = client.get("/api/culture-content/history", headers=headers)

        assert history.status_code == 200, history.text
        history_ids = {item["content_id"] for item in history.json()["contents"]}
        assert _legacy_id not in history_ids, "legacy ownerless row leaked into history"

        factory = get_session_factory()

        async def _fetch_legacy_row():
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return (
                    (
                        await db.execute(
                            select(CultureContent).where(
                                CultureContent.tenant_id == tenant_id,
                                CultureContent.created_by.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )

        legacy_row = asyncio.run(_fetch_legacy_row())
        assert legacy_row is not None, "seed did not create the ownerless row"
        detail = client.get(f"/api/culture-content/{legacy_row.id}", headers=headers)
        assert detail.status_code == 404, detail.text
    finally:
        asyncio.run(_cleanup(tenant_id))
