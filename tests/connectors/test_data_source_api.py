"""Data-source connector API: credential registration, OAuth, guarded sync.

Security contract under test:
- credentials are accepted (first-batch platforms) but NEVER returned,
- oauth state transitions are audited,
- sync refuses paused/revoked/unauthorized sources.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.connector import ConnectorIdentityBinding, OAuthNonce
from app.data.models.data_source import DataSource
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
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


def _payload(**overrides) -> dict:
    payload = {
        "name": "华东团队群消息",
        "platform": "wecom",
        "purpose": "汇总员工反馈主题",
        "authorized_scope": "仅授权的三个团队群",
        "content_types": ["messages"],
        "data_destination": "员工声音分析工作区",
    }
    payload.update(overrides)
    return payload


def _wecom_callback_config() -> dict[str, str]:
    return {
        "corp_id": "ww-test-corp",
        "agent_id": "1000002",
        "corp_secret": "test-corp-secret",
        "callback_token": "CallbackToken1",
        "encoding_aes_key": "a" * 43,
    }


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        # OAuth nonces carry an FK to data_sources; delete them first so the
        # FK from oauth_nonces -> data_sources does not reject the teardown.
        await db.execute(delete(OAuthNonce).where(OAuthNonce.tenant_id == tenant_id))
        await db.execute(delete(ConnectorIdentityBinding).where(ConnectorIdentityBinding.tenant_id == tenant_id))
        await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.commit()


def test_credential_is_registered_but_never_returned(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    secret = "corp-app-secret-material"
    try:
        created = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(credential=secret, oauth_app_id="ww10086"),
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["oauth_state"] == "none"
        assert "credential" not in body
        assert secret not in created.text, "credential plaintext leaked into the response"

        listed = client.get("/api/data-sources", headers=headers)
        assert listed.status_code == 200
        assert secret not in listed.text, "credential plaintext leaked into the list"
        assert all(not str(value).startswith("gAAA") for value in _flatten(listed.json())), "ciphertext leaked"

        # Ciphertext exists in storage, scoped to this tenant's key.
        async def load_ciphertext() -> bytes | None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return (await db.execute(select(DataSource.credential_encrypted))).scalar()

        ciphertext = asyncio.run(load_ciphertext())
        assert ciphertext is not None and secret.encode() not in ciphertext
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_structured_scope_is_persisted_for_server_side_sync_enforcement(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    scope = {"chat_ids": ["chat-east", "chat-hr"], "folder_ids": []}
    try:
        created = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(authorized_scope_json=scope),
        )
        assert created.status_code == 200, created.text
        assert created.json()["authorized_scope_json"] == scope

        listed = client.get("/api/data-sources", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["sources"][0]["authorized_scope_json"] == scope
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_employee_request_event_route_is_persisted(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        created = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                event_route="employee_request",
                authorized_scope_json={"chat_ids": ["chat-hr"], "folder_ids": []},
            ),
        )
        assert created.status_code == 200, created.text
        assert created.json()["event_route"] == "employee_request"

        listed = client.get("/api/data-sources", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["sources"][0]["event_route"] == "employee_request"
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_wecom_employee_request_route_allows_a_direct_app_message_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        response = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(event_route="employee_request", authorized_scope_json=None),
        )
        assert response.status_code == 200, response.text
        assert response.json()["authorized_scope_json"] is None
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_stores_wecom_callback_configuration_without_secret_leak(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    configuration = _wecom_callback_config()
    try:
        source = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                event_route="employee_request",
                authorized_scope_json={"chat_ids": ["chat-hr"], "folder_ids": []},
            ),
        )
        assert source.status_code == 200, source.text
        source_id = source.json()["source_id"]

        stored = client.put(
            f"/api/data-sources/{source_id}/wecom-callback-config",
            headers=headers,
            json=configuration,
        )
        assert stored.status_code == 200, stored.text
        assert stored.json() == {
            "source_id": source_id,
            "configured": True,
            "corp_id": configuration["corp_id"],
            "agent_id": configuration["agent_id"],
            "callback_path": f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
        }

        listed = client.get("/api/data-sources", headers=headers)
        assert listed.status_code == 200, listed.text
        for secret in (
            configuration["corp_secret"],
            configuration["callback_token"],
            configuration["encoding_aes_key"],
        ):
            assert secret not in listed.text
            assert secret not in stored.text
        assert (
            listed.json()["sources"][0]["wecom_callback_path"]
            == f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}"
        )
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_wecom_callback_configuration_rejects_non_wecom_source(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        source = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                platform="feishu",
                event_route="employee_request",
                authorized_scope_json=None,
            ),
        )
        assert source.status_code == 200, source.text

        rejected = client.put(
            f"/api/data-sources/{source.json()['source_id']}/wecom-callback-config",
            headers=headers,
            json=_wecom_callback_config(),
        )
        assert rejected.status_code == 422, rejected.text
        assert "企业微信" in rejected.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_admin_can_bind_platform_identity_to_employee_for_request_intake(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id = str(uuid4())
    employee_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}

    async def seed_employee() -> None:
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                User(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Connector employee",
                    email=f"{employee_id}@example.test",
                    hashed_password="test-only",
                    role="employee",
                )
            )
            await db.commit()

    try:
        asyncio.run(seed_employee())
        source = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                event_route="employee_request",
                authorized_scope_json={"chat_ids": ["chat-hr"], "folder_ids": []},
            ),
        )
        assert source.status_code == 200, source.text
        source_id = source.json()["source_id"]

        bound = client.post(
            f"/api/data-sources/{source_id}/identity-bindings",
            headers=headers,
            json={"external_user_id": "wecom-user-10086", "user_id": employee_id},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json() == {
            "source_id": source_id,
            "external_user_id": "wecom-user-10086",
            "user_id": employee_id,
        }
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_platform_identity_binding_rejects_non_employee_user(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id = str(uuid4())
    hrbp_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}

    async def seed_hrbp() -> None:
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                User(
                    id=hrbp_id,
                    tenant_id=tenant_id,
                    name="Connector HRBP",
                    email=f"{hrbp_id}@example.test",
                    hashed_password="test-only",
                    role="hrbp",
                )
            )
            await db.commit()

    try:
        asyncio.run(seed_hrbp())
        source = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                event_route="employee_request",
                authorized_scope_json={"chat_ids": ["chat-hr"], "folder_ids": []},
            ),
        )
        assert source.status_code == 200, source.text

        bound = client.post(
            f"/api/data-sources/{source.json()['source_id']}/identity-bindings",
            headers=headers,
            json={"external_user_id": "wecom-hrbp-10086", "user_id": hrbp_id},
        )
        assert bound.status_code == 422, bound.text
        assert "员工角色" in bound.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_credential_requires_app_id_on_first_batch_platforms(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        response = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(credential="secret-without-app-id"),
        )
        assert response.status_code == 422, response.text
        assert "应用 ID" in response.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_non_first_batch_platform_still_rejects_credentials(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        response = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(
                platform="dingtalk",
                credential="dingtalk-secret",
            ),
        )
        assert response.status_code == 422, response.text
        assert "尚未开放凭据登记" in response.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_start_requires_registered_credential(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        created = client.post("/api/data-sources", headers=headers, json=_payload())
        assert created.status_code == 200, created.text
        source_id = created.json()["source_id"]

        response = client.post(
            f"/api/data-sources/{source_id}/oauth-start",
            headers=headers,
            json={"redirect_uri": "https://app.example/callback"},
        )
        assert response.status_code == 422, response.text
        assert "登记应用凭据" in response.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_oauth_start_returns_consent_url_and_audits(client: TestClient) -> None:
    tenant_id = str(uuid4())
    admin_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, admin_id)}"}
    try:
        created = client.post(
            "/api/data-sources",
            headers=headers,
            json=_payload(credential="corp-secret", oauth_app_id="ww10086"),
        )
        source_id = created.json()["source_id"]

        started = client.post(
            f"/api/data-sources/{source_id}/oauth-start",
            headers=headers,
            json={"redirect_uri": "https://app.example/callback"},
        )
        assert started.status_code == 200, started.text
        assert "authorize" in started.json()["authorize_url"]
        assert "state=" in started.json()["authorize_url"]

        async def load_actions() -> list[str]:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                return list(
                    (
                        await db.execute(
                            select(AuditLog.scenario_id).where(
                                AuditLog.tenant_id == tenant_id,
                                AuditLog.user_id == admin_id,
                            )
                        )
                    ).scalars()
                )

        actions = asyncio.run(load_actions())
        assert "data_source.oauth_started" in actions
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_sync_refuses_unauthorized_or_paused_sources(client: TestClient) -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_token(tenant_id, str(uuid4()))}"}
    try:
        created = client.post("/api/data-sources", headers=headers, json=_payload())
        source_id = created.json()["source_id"]

        unauthorized = client.post(f"/api/data-sources/{source_id}/sync", headers=headers, json={})
        assert unauthorized.status_code == 422, unauthorized.text
        assert "尚未完成平台授权" in unauthorized.json()["message"]

        paused = client.post(f"/api/data-sources/{source_id}/pause", headers=headers)
        assert paused.status_code == 200
        paused_sync = client.post(f"/api/data-sources/{source_id}/sync", headers=headers, json={})
        assert paused_sync.status_code == 422
        assert "已暂停" in paused_sync.json()["message"]
    finally:
        asyncio.run(_cleanup(tenant_id))


def _flatten(value, _acc=None):
    if _acc is None:
        _acc = []
    if isinstance(value, dict):
        for v in value.values():
            _flatten(v, _acc)
    elif isinstance(value, list):
        for v in value:
            _flatten(v, _acc)
    else:
        _acc.append(value)
    return _acc
