"""Pure protocol helpers for WeCom self-built application callbacks.

This module deliberately has no database dependency: callers provide the
source-scoped callback configuration only after loading it under tenant scope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

from app.shared.errors import AppError


class WeComCallbackRejectedError(AppError):
    """A forged or malformed WeCom callback that must not reach persistence."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="WECOM_CALLBACK_REJECTED", status_code=403)


@dataclass(frozen=True)
class WeComInboundMessage:
    external_event_id: str
    event_type: str
    external_user_id: str
    content: str
    occurred_at: str | None


def _verify_signature(
    msg_signature: str,
    callback_token: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
) -> None:
    expected = hashlib.sha1("".join(sorted([callback_token, timestamp, nonce, encrypted])).encode()).hexdigest()
    if not hmac.compare_digest(expected, msg_signature):
        raise WeComCallbackRejectedError("企业微信回调签名校验失败")


def _decrypt_payload(encrypted: str, encoding_aes_key: str, corp_id: str) -> bytes:
    try:
        key = base64.b64decode(encoding_aes_key + "=", validate=True)
        if len(key) != 32:
            raise ValueError("invalid AES key length")
        cipher_bytes = base64.b64decode(encrypted, validate=True)

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(cipher_bytes) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except Exception as exc:
        raise WeComCallbackRejectedError("企业微信回调解密失败") from exc

    if len(plaintext) < 20:
        raise WeComCallbackRejectedError("企业微信回调加密内容不完整")
    message_length = int.from_bytes(plaintext[16:20], "big")
    message_end = 20 + message_length
    if message_length < 1 or message_end > len(plaintext):
        raise WeComCallbackRejectedError("企业微信回调消息长度无效")
    try:
        received_corp_id = plaintext[message_end:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WeComCallbackRejectedError("企业微信回调企业 ID 无效") from exc
    if received_corp_id != corp_id:
        raise WeComCallbackRejectedError("企业微信回调企业 ID 不匹配")
    return plaintext[20:message_end]


def verify_wecom_url(
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
    callback_token: str,
    encoding_aes_key: str,
    corp_id: str,
) -> bytes:
    """Verify a GET challenge and return its exact decrypted payload bytes."""
    _verify_signature(msg_signature, callback_token, timestamp, nonce, encrypted)
    return _decrypt_payload(encrypted, encoding_aes_key, corp_id)


def _required_text(root: ElementTree.Element, field: str) -> str:
    value = root.findtext(field)
    if value is None or not value.strip():
        raise WeComCallbackRejectedError(f"企业微信回调缺少 {field}")
    return value.strip()


def parse_wecom_callback(
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
    callback_token: str,
    encoding_aes_key: str,
    corp_id: str,
    agent_id: str,
) -> WeComInboundMessage:
    """Verify, decrypt, and parse one supported direct text message callback."""
    _verify_signature(msg_signature, callback_token, timestamp, nonce, encrypted)
    plaintext = _decrypt_payload(encrypted, encoding_aes_key, corp_id)
    try:
        root = ElementTree.fromstring(plaintext)
    except ElementTree.ParseError as exc:
        raise WeComCallbackRejectedError("企业微信回调 XML 无效") from exc

    if _required_text(root, "ToUserName") != corp_id:
        raise WeComCallbackRejectedError("企业微信回调企业 ID 不匹配")
    if _required_text(root, "AgentID") != agent_id:
        raise WeComCallbackRejectedError("企业微信回调 AgentID 不匹配")
    if _required_text(root, "MsgType") != "text":
        raise WeComCallbackRejectedError("企业微信员工请求入口仅接收文本消息")

    message_id = _required_text(root, "MsgId")
    sender = _required_text(root, "FromUserName")
    content = _required_text(root, "Content")
    return WeComInboundMessage(
        external_event_id=f"msg:{message_id}",
        event_type="text",
        external_user_id=sender,
        content=content,
        occurred_at=_required_text(root, "CreateTime"),
    )
