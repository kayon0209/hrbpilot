"""Webhook ingress for WeCom / Feishu — signature verification first.

Provider callbacks are unauthenticated by definition, so the ingress never
trusts the body until the platform's own verification passes:

- WeCom callbacks carry msg_signature = SHA1(sort(token, timestamp, nonce,
  encrypt)) over AES-encrypted payloads (企业微信回调加密协议). The decrypt
  output is then deduplicated via connector_event_log before any processing.
- Feishu events require the challenge handshake (url_verification) and carry
  an Authorization header signed with the app's verification token; the
  v2 schema is signed as HMAC-SHA256 over timestamp:nonce:body.

Every verified event goes through consume_event() — a redelivered event is
counted and dropped, never re-processed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from app.connectors.sync import consume_event
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)


class WebhookRejectedError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="WEBHOOK_REJECTED", status_code=403)


WebhookRejected = WebhookRejectedError  # backwards-compatible alias


@dataclass
class VerifiedEvent:
    tenant_id: str
    source_id: str
    external_event_id: str
    event_type: str
    payload: dict


def verify_wecom_signature(msg_signature: str, token: str, timestamp: str, nonce: str, encrypted: str) -> None:
    """WeCom callback signature: SHA1 over the sorted quadruple."""
    raw = sorted([token, timestamp, nonce, encrypted])
    expected = hashlib.sha1("".join(raw).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected, msg_signature):
        raise WebhookRejectedError("企业微信回调签名校验失败")


def decrypt_wecom_payload(
    encrypted_b64: str, aes_key_b64: str, corpid: str
) -> dict:
    """Decrypt a WeCom callback body (AES-256-CBC, key derived from base64)."""
    try:
        key = base64.b64decode(aes_key_b64 + "=")
        iv = key[:16]

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(base64.b64decode(encrypted_b64)) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    except Exception as exc:
        logger.warning("wecom_callback_decrypt_failed")
        raise WebhookRejectedError("企业微信回调解密失败") from exc

    # WeCom format: 16 random bytes + 4-byte message length + message + corpid
    length = int.from_bytes(plain[16:20], "big")
    message = plain[20 : 20 + length]
    attached_corpid = plain[20 + length :].decode("utf-8")
    if attached_corpid != corpid:
        raise WebhookRejectedError("企业微信回调 corpid 不匹配")
    payload: dict = json.loads(message.decode("utf-8"))
    return payload


def verify_feishu_signature(authorization: str, verification_token: str, timestamp: str, nonce: str, body: str) -> None:
    """Feishu v2 event signature: HMAC-SHA256 over '{timestamp}{nonce}{body}'."""
    payload = f"{timestamp}{nonce}{body}".encode()
    expected = hmac.new(verification_token.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, authorization):
        raise WebhookRejectedError("飞书事件签名校验失败")


def _wecom_external_id(payload: dict) -> str:
    """Derive a stable idempotency key for a WeCom callback.

    Message callbacks carry a globally-unique ``MsgId`` — prefer it, so two
    messages landing in the same second are never conflated into a replay.
    Event callbacks carry no MsgId, so bind to sender+event+key+time, which
    sharply reduces (but not eliminates) cross-event collisions.  Only fall
    back to a random nonce when nothing identity-bearing exists.
    """
    msg_id = payload.get("MsgId")
    if msg_id:
        return f"msg:{msg_id}"

    parts = [
        str(payload.get("Event", payload.get("MsgType", "callback"))),
        str(payload.get("FromUserName", "")),
        str(payload.get("EventKey", "")),
        str(payload.get("CreateTime", "")),
    ]
    token = ":".join(parts).strip(":")
    return token or secrets.token_urlsafe(16)


async def ingest_wecom_callback(
    tenant_id: str,
    source_id: str,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
    token: str,
    aes_key_b64: str,
    corpid: str,
) -> VerifiedEvent | None:
    """Verify + decrypt + dedupe one WeCom callback. None when a replay."""
    verify_wecom_signature(msg_signature, token, timestamp, nonce, encrypted)
    payload = decrypt_wecom_payload(encrypted, aes_key_b64, corpid)

    external_id = _wecom_external_id(payload)
    event_type = str(payload.get("Event", payload.get("MsgType", "callback")))
    first_seen = await consume_event(tenant_id, source_id, external_id, event_type, payload)
    if not first_seen:
        logger.info("wecom_callback_replay_dropped", tenant_id=tenant_id, source_id=source_id)
        return None
    return VerifiedEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        external_event_id=external_id,
        event_type=event_type,
        payload=payload,
    )


async def ingest_feishu_event(
    tenant_id: str,
    source_id: str,
    *,
    authorization: str,
    verification_token: str,
    timestamp: str,
    nonce: str,
    body: str,
) -> dict | VerifiedEvent | None:
    """Verify one Feishu event. Returns the challenge answer for url_verification,
    a VerifiedEvent for events, None for replays."""
    verify_feishu_signature(authorization, verification_token, timestamp, nonce, body)
    payload = json.loads(body)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    header = payload.get("header", {})
    external_id = str(header.get("event_id", "")) or secrets.token_urlsafe(16)
    event_type = str(header.get("event_type", "event"))

    first_seen = await consume_event(tenant_id, source_id, external_id, event_type, payload)
    if not first_seen:
        logger.info("feishu_event_replay_dropped", tenant_id=tenant_id, source_id=source_id)
        return None
    return VerifiedEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        external_event_id=external_id,
        event_type=event_type,
        payload=payload,
    )
