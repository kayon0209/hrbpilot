"""Durable audit regressions for sensitive administrative actions."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.config.settings import settings
from app.main import create_app

_TENANT = "06c87e30-4abf-40ca-9805-3c8b44cc5fd5"
_ADMIN = "10000000-0000-4000-8000-000000000003"


def _token() -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": _ADMIN,
                "role": "admin",
                "tenant_id": _TENANT,
                "email": "admin@example.com",
                "type": "access",
                "jti": "audit-test",
                "iss": "hrbp-ai-workbench",
                "aud": "hrbp-ai-workbench",
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_data_source_lifecycle_is_queryable_in_durable_audit(client):
    headers = {"Authorization": f"Bearer {_token()}"}
    created = client.post(
        "/api/data-sources",
        headers=headers,
        json={
            "name": "审计回归接入",
            "platform": "feishu",
            "purpose": "验证敏感操作审计",
            "authorized_scope": "测试目录",
            "content_types": ["documents"],
            "data_destination": "测试知识库",
        },
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["source_id"]

    assert client.post(f"/api/data-sources/{source_id}/pause", headers=headers).status_code == 200
    revoked = client.post(
        f"/api/data-sources/{source_id}/revoke",
        headers=headers,
        json={"reason": "审计回归完成"},
    )
    assert revoked.status_code == 200

    audit = client.get(f"/api/audit/events?object_id={source_id}", headers=headers)
    assert audit.status_code == 200, audit.text
    actions = {event["action"] for event in audit.json()["events"]}
    assert {"data_source.created", "data_source.paused", "data_source.revoked"} <= actions
    assert all(event["actor_id"] == _ADMIN for event in audit.json()["events"])
