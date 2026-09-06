"""Webhook ingress security: signature verification and replay protection.

Replay/idempotency tests commit to the connector event log — needs a live
PostgreSQL with migration 018 applied, hence integration.
"""

import base64
import hashlib
import json
import secrets
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from sqlalchemy import delete

from app.connectors.webhooks import (
    WebhookRejected,
    decrypt_wecom_payload,
    ingest_feishu_event,
    ingest_wecom_callback,
    verify_feishu_signature,
    verify_wecom_signature,
)
from app.data.database import get_session_factory
from app.data.models.connector import ConnectorEventLog
from app.data.models.data_source import DataSource


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


pytestmark = pytest.mark.integration

AES_KEY = base64.b64encode(bytes(range(32))).decode().rstrip("=")
TOKEN = "callback-token"
CORPID = "ww1234567890"


def test_wecom_signature_must_match() -> None:
    encrypted = _encrypt_wecom({"MsgType": "event", "Event": "subscribe"}, AES_KEY, CORPID)
    good = _sign_wecom(TOKEN, "1700000000", "nonce-1", encrypted)
    verify_wecom_signature(good, TOKEN, "1700000000", "nonce-1", encrypted)

    with pytest.raises(WebhookRejected):
        verify_wecom_signature("deadbeef", TOKEN, "1700000000", "nonce-1", encrypted)
    # a body tampered after signing must fail too
    with pytest.raises(WebhookRejected):
        verify_wecom_signature(good, TOKEN, "1700000000", "nonce-1", encrypted + "x")


def test_wecom_external_id_prefers_msgid_for_message_callbacks() -> None:
    """Two messages in the same second must not be conflated by a coarse key."""
    from app.connectors.webhooks import _wecom_external_id

    # Message callbacks carry a globally-unique MsgId → distinct keys.
    first = _wecom_external_id({"MsgId": "msg-1", "CreateTime": 1700000000, "MsgType": "text"})
    second = _wecom_external_id({"MsgId": "msg-2", "CreateTime": 1700000000, "MsgType": "text"})
    assert first != second
    assert first.startswith("msg:") and second.startswith("msg:")

    # Replay of the same message keeps the same key.
    assert _wecom_external_id({"MsgId": "msg-1", "CreateTime": 1700000000}) == first


def test_wecom_decrypt_checks_corpid() -> None:
    encrypted = _encrypt_wecom({"MsgType": "event"}, AES_KEY, "ww-other-corp")
    with pytest.raises(WebhookRejected):
        decrypt_wecom_payload(encrypted, AES_KEY, CORPID)


def test_wecom_external_id_binds_events_to_sender_event_and_time() -> None:
    from app.connectors.webhooks import _wecom_external_id

    # No MsgId on events → bind to sender+event+key+time. Same payload is stable
    # (replay detected), a different event from a different sender is distinct.
    a = _wecom_external_id({"Event": "subscribe", "FromUserName": "u-1", "EventKey": "", "CreateTime": 1700000000})
    assert a == _wecom_external_id(
        {"Event": "subscribe", "FromUserName": "u-1", "EventKey": "", "CreateTime": 1700000000}
    )
    b = _wecom_external_id({"Event": "click", "FromUserName": "u-2", "EventKey": "k", "CreateTime": 1700000000})
    assert a != b


def test_feishu_signature_must_match() -> None:
    body = '{"type":"url_verification","challenge":"ch-1"}'
    good = __import__("hmac").new(TOKEN.encode(), ("1700000000" + "n1" + body).encode(), hashlib.sha256).hexdigest()
    verify_feishu_signature(good, TOKEN, "1700000000", "n1", body)
    with pytest.raises(WebhookRejected):
        verify_feishu_signature("bad", TOKEN, "1700000000", "n1", body)


async def _seed_and_cleanup(tenant_id: str, source_id: str):
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            DataSource(
                id=source_id,
                tenant_id=tenant_id,
                name="webhook 测试",
                platform="wecom",
                purpose="测试",
                authorized_scope="测试",
                content_types='["documents"]',
                data_destination="测试",
                created_by="webhook-test",
            )
        )
        await db.commit()

    async def cleanup():
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(
                delete(ConnectorEventLog).where(
                    ConnectorEventLog.tenant_id == tenant_id,
                    ConnectorEventLog.source_id == source_id,
                )
            )
            await db.execute(delete(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
            await db.commit()

    return cleanup


@pytest.mark.asyncio
async def test_wecom_callback_ingest_is_idempotent() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    cleanup = await _seed_and_cleanup(tenant_id, source_id)
    try:
        message = {"ToUserName": "ww-app", "CreateTime": 1700000000, "Event": "subscribe"}
        encrypted = _encrypt_wecom(message, AES_KEY, CORPID)
        signature = _sign_wecom(TOKEN, "1700000000", "nonce-1", encrypted)

        first = await ingest_wecom_callback(
            tenant_id,
            source_id,
            msg_signature=signature,
            timestamp="1700000000",
            nonce="nonce-1",
            encrypted=encrypted,
            token=TOKEN,
            aes_key_b64=AES_KEY,
            corpid=CORPID,
        )
        assert first is not None and first.payload["Event"] == "subscribe"

        replay = await ingest_wecom_callback(
            tenant_id,
            source_id,
            msg_signature=signature,
            timestamp="1700000000",
            nonce="nonce-1",
            encrypted=encrypted,
            token=TOKEN,
            aes_key_b64=AES_KEY,
            corpid=CORPID,
        )
        assert replay is None, "replayed callback must be dropped"
    finally:
        await cleanup()


@pytest.mark.asyncio
async def test_wecom_distinct_messages_same_second_are_both_consumed() -> None:
    """The idempotency key must not collapse distinct messages sharing a second."""
    tenant_id, source_id = str(uuid4()), str(uuid4())
    cleanup = await _seed_and_cleanup(tenant_id, source_id)
    try:
        count_uniques = 0
        for msg_id in ("m-a", "m-b"):
            message = {
                "ToUserName": "ww-app",
                "CreateTime": 1700000000,
                "MsgType": "text",
                "MsgId": msg_id,
                "FromUserName": f"user-{msg_id}",
            }
            encrypted = _encrypt_wecom(message, AES_KEY, CORPID)
            signature = _sign_wecom(TOKEN, "1700000000", f"nonce-{msg_id}", encrypted)
            result = await ingest_wecom_callback(
                tenant_id,
                source_id,
                msg_signature=signature,
                timestamp="1700000000",
                nonce=f"nonce-{msg_id}",
                encrypted=encrypted,
                token=TOKEN,
                aes_key_b64=AES_KEY,
                corpid=CORPID,
            )
            assert result is not None
            # distinct msgid ⇒ distinct idempotency key, even within the same second
            assert result.external_event_id == f"msg:{msg_id}"
            if result is not None:
                count_uniques += 1

        assert count_uniques == 2, "two distinct messages in one second must both be consumed"
    finally:
        await cleanup()


@pytest.mark.asyncio
async def test_feishu_url_verification_handshake() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    cleanup = await _seed_and_cleanup(tenant_id, source_id)
    try:
        body = json.dumps({"type": "url_verification", "challenge": "answer-42"})
        signature = (
            __import__("hmac").new(TOKEN.encode(), ("1700000000" + "n1" + body).encode(), hashlib.sha256).hexdigest()
        )
        result = await ingest_feishu_event(
            tenant_id,
            source_id,
            authorization=signature,
            verification_token=TOKEN,
            timestamp="1700000000",
            nonce="n1",
            body=body,
        )
        assert result == {"challenge": "answer-42"}
    finally:
        await cleanup()


@pytest.mark.asyncio
async def test_feishu_event_replay_is_dropped() -> None:
    tenant_id, source_id = str(uuid4()), str(uuid4())
    cleanup = await _seed_and_cleanup(tenant_id, source_id)
    try:
        body = json.dumps(
            {
                "header": {"event_id": "evt-abc", "event_type": "im.message.receive_v1"},
                "event": {"message": {}},
            }
        )
        signature = (
            __import__("hmac").new(TOKEN.encode(), ("1700000000" + "n1" + body).encode(), hashlib.sha256).hexdigest()
        )
        kwargs = dict(
            authorization=signature,
            verification_token=TOKEN,
            timestamp="1700000000",
            nonce="n1",
            body=body,
        )
        first = await ingest_feishu_event(tenant_id, source_id, **kwargs)
        assert first is not None and first.event_type == "im.message.receive_v1"

        replay = await ingest_feishu_event(tenant_id, source_id, **kwargs)
        assert replay is None, "redelivered event must not re-trigger processing"
    finally:
        await cleanup()
