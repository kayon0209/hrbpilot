"""HTTP acceptance for the protocol-correct WeCom URL verification endpoint."""

import asyncio
import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.connector import ConnectorEventLog, ConnectorIdentityBinding, ConnectorIntakeEvent
from app.data.models.data_source import DataSource
from app.data.models.infra import AuditLog
from app.data.models.scenarios import EmployeeRequest
from app.data.models.user import User
from app.main import create_app

TOKEN = "CallbackToken1"
AES_KEY = base64.b64encode(bytes(range(32))).decode().rstrip("=")
CORP_ID = "ww-test-corp"
AGENT_ID = "1000002"


def _admin_token(tenant_id: str) -> str:
    now = datetime.now(UTC)
    return str(jwt.encode({
        "sub": str(uuid4()), "role": "admin", "tenant_id": tenant_id,
        "email": "admin@example.test", "type": "access", "jti": str(uuid4()),
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
        "exp": now + timedelta(minutes=15), "iat": now,
    }, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def _encrypt_challenge(challenge: bytes) -> str:
    key = base64.b64decode(AES_KEY + "=")
    plaintext = secrets.token_bytes(16) + len(challenge).to_bytes(4, "big") + challenge + CORP_ID.encode()
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _signature(encrypted: str, nonce: str = "nonce-1") -> str:
    return hashlib.sha1("".join(sorted([TOKEN, "1700000000", nonce, encrypted])).encode()).hexdigest()


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        for model in (EmployeeRequest, ConnectorIntakeEvent, ConnectorIdentityBinding, ConnectorEventLog, AuditLog, User, DataSource):
            await db.execute(delete(model).where(model.tenant_id == tenant_id))
        await db.commit()


def _source_payload() -> dict:
    return {
        "name": "企微员工入口", "platform": "wecom", "purpose": "员工 HR 事项",
        "authorized_scope": "自建应用直接消息", "authorized_scope_json": None,
        "content_types": ["messages"], "event_route": "employee_request", "data_destination": "员工请求",
    }


def _callback_config() -> dict:
    return {
        "corp_id": CORP_ID, "agent_id": AGENT_ID, "corp_secret": "test-corp-secret",
        "callback_token": TOKEN, "encoding_aes_key": AES_KEY,
    }


def test_wecom_url_challenge_returns_plaintext_without_authentication() -> None:
    tenant_id = str(uuid4())
    headers = {"Authorization": f"Bearer {_admin_token(tenant_id)}"}
    try:
        with TestClient(create_app()) as client:
            source = client.post("/api/data-sources", headers=headers, json=_source_payload())
            assert source.status_code == 200, source.text
            source_id = source.json()["source_id"]
            configured = client.put(f"/api/data-sources/{source_id}/wecom-callback-config", headers=headers, json=_callback_config())
            assert configured.status_code == 200, configured.text

            encrypted = _encrypt_challenge(b"callback-ok")
            signature = _signature(encrypted)
            response = client.get(
                f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
                params={"msg_signature": signature, "timestamp": "1700000000", "nonce": "nonce-1", "echostr": encrypted},
            )
    finally:
        asyncio.run(_cleanup(tenant_id))

    assert response.status_code == 200, response.text
    assert response.content == b"callback-ok"
    assert response.headers["content-type"].startswith("text/plain")


def test_wecom_text_message_creates_bound_employee_request_once() -> None:
    tenant_id = str(uuid4())
    employee_id = str(uuid4())
    external_user_id = "wecom-employee-10086"
    headers = {"Authorization": f"Bearer {_admin_token(tenant_id)}"}

    async def seed_employee() -> None:
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(User(id=employee_id, tenant_id=tenant_id, name="Callback Employee", email=f"{employee_id}@example.test", hashed_password="test-only", role="employee"))
            await db.commit()

    try:
        asyncio.run(seed_employee())
        with TestClient(create_app()) as client:
            source = client.post("/api/data-sources", headers=headers, json=_source_payload())
            assert source.status_code == 200, source.text
            source_id = source.json()["source_id"]
            configured = client.put(f"/api/data-sources/{source_id}/wecom-callback-config", headers=headers, json=_callback_config())
            assert configured.status_code == 200, configured.text
            bound = client.post(f"/api/data-sources/{source_id}/identity-bindings", headers=headers, json={"external_user_id": external_user_id, "user_id": employee_id})
            assert bound.status_code == 200, bound.text

            inner_xml = (
                f"<xml><ToUserName>{CORP_ID}</ToUserName><FromUserName>{external_user_id}</FromUserName>"
                f"<CreateTime>1700000000</CreateTime><MsgType>text</MsgType><Content>我要申请调休</Content>"
                f"<MsgId>message-10086</MsgId><AgentID>{AGENT_ID}</AgentID></xml>"
            ).encode()
            encrypted = _encrypt_challenge(inner_xml)
            signature = _signature(encrypted)
            callback_url = f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}"
            params = {"msg_signature": signature, "timestamp": "1700000000", "nonce": "nonce-1"}
            body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"
            accepted = client.post(callback_url, params=params, content=body, headers={"Content-Type": "application/xml"})
            assert accepted.status_code == 200, accepted.text
            replay = client.post(callback_url, params=params, content=body, headers={"Content-Type": "application/xml"})
            assert replay.status_code == 200, replay.text

        async def verify() -> None:
            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                requests = (await db.execute(EmployeeRequest.__table__.select().where(EmployeeRequest.tenant_id == tenant_id))).mappings().all()
                events = (await db.execute(ConnectorEventLog.__table__.select().where(ConnectorEventLog.tenant_id == tenant_id))).mappings().all()
            assert len(requests) == 1
            assert requests[0]["created_by"] == employee_id
            assert requests[0]["description"] == "我要申请调休"
            assert len(events) == 1
            assert events[0]["status"] == "processed"
            assert events[0]["replay_count"] == 1

        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup(tenant_id))
