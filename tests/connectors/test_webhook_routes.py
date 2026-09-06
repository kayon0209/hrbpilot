"""CONN-07: webhook ingress routes exist, are unauthenticated, and verify
the platform signature before consuming any event."""

import base64
import hashlib
import hmac
import json
import os
import secrets
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.data.database import get_session_factory
from app.data.models.connector import ConnectorEventLog
from app.data.models.data_source import DataSource
from app.main import create_app

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS") and not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_DB_SECURITY_TESTS=true for PG verification")


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _sign_wecom(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    raw = sorted([token, timestamp, nonce, encrypted])
    return hashlib.sha1("".join(raw).encode("utf-8")).hexdigest()


def _encrypt_wecom(message: dict, aes_key_b64: str, corpid: str) -> str:
    key = base64.b64decode(aes_key_b64 + "=")
    iv = key[:16]
    msg = json.dumps(message, ensure_ascii=False).encode("utf-8")
    plain = secrets.token_bytes(16) + len(msg).to_bytes(4, "big") + msg + corpid.encode("utf-8")
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


async def _seed_source(tenant_id: str, secret: str, corpid: str) -> str:
    from app.connectors.credentials import encrypt_credential

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = DataSource(
            id=str(uuid4()),
            tenant_id=tenant_id,
            name="回调路由源",
            platform="wecom",
            purpose="route",
            authorized_scope="x",
            content_types='["messages"]',
            data_destination="x",
            created_by="webhook-route",
            oauth_state="connected",
            oauth_app_id=corpid,
            credential_encrypted=encrypt_credential(tenant_id, secret),
        )
        db.add(row)
        await db.commit()
        return row.id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        await db.execute(delete(ConnectorEventLog).where(ConnectorEventLog.tenant_id == tenant_id))
        await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id))
        await db.commit()


def test_wecom_webhook_route_is_unauthenticated_and_verifies_signature(client: TestClient) -> None:
    _require()
    tenant_id = str(uuid4())
    # The registered credential is the WeCom callback secret material: the AES
    # key (base64) that both decrypts the body and seeds the SHA1 signature.
    aes_key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    secret = aes_key
    corpid = "ww-route-corp"
    import asyncio

    source_id = asyncio.run(_seed_source(tenant_id, secret, corpid))
    try:
        # A signed callback with NO Authorization header reaches the route
        # (provider callbacks carry no JWT).
        message = {"ToUserName": corpid, "CreateTime": 1700000000, "MsgType": "event", "Event": "subscribe"}
        encrypted = _encrypt_wecom(message, aes_key, corpid)
        signature = _sign_wecom(aes_key, "1700000000", "nonce-1", encrypted)

        response = client.post(
            f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
            data={
                "msg_signature": signature,
                "timestamp": "1700000000",
                "nonce": "nonce-1",
                "encrypt": encrypted,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["verified"] is True

        # Tampered signature must be rejected before any state is consumed.
        bad = client.post(
            f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
            data={
                "msg_signature": "deadbeef",
                "timestamp": "1700000000",
                "nonce": "nonce-1",
                "encrypt": encrypted,
            },
        )
        assert bad.status_code == 403, bad.text

        # Replay of the SAME verified event is dropped (idempotent).
        replay = client.post(
            f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
            data={
                "msg_signature": signature,
                "timestamp": "1700000000",
                "nonce": "nonce-1",
                "encrypt": encrypted,
            },
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_feishu_webhook_route_challenge_handshake(client: TestClient) -> None:
    _require()
    tenant_id = str(uuid4())
    secret = "feishu-verification-token"
    import asyncio

    source_id = asyncio.run(_seed_source(tenant_id, secret, "cli-route"))
    try:
        body = json.dumps({"type": "url_verification", "challenge": "challenge-answer"})
        signature = hmac.new(secret.encode(), (f"1700000000n1{body}").encode(), hashlib.sha256).hexdigest()

        response = client.post(
            f"/api/connector-webhooks/feishu/{tenant_id}/{source_id}",
            content=body,
            headers={
                "X-Lark-Signature": signature,
                "X-Lark-Request-Timestamp": "1700000000",
                "X-Lark-Request-Nonce": "n1",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["challenge"] == "challenge-answer"
    finally:
        asyncio.run(_cleanup(tenant_id))
