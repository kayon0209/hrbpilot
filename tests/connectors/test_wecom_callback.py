"""Protocol vectors for the real WeCom self-built application callback wire format."""

import base64
import hashlib
import secrets

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.connectors.wecom_callback import (
    WeComCallbackRejectedError,
    parse_wecom_callback,
    verify_wecom_url,
)

TOKEN = "CallbackToken1"
AES_KEY = base64.b64encode(bytes(range(32))).decode().rstrip("=")
CORP_ID = "ww-test-corp"
AGENT_ID = "1000002"


def _encrypt(message: bytes, *, corp_id: str = CORP_ID) -> str:
    key = base64.b64decode(AES_KEY + "=")
    plaintext = secrets.token_bytes(16) + len(message).to_bytes(4, "big") + message + corp_id.encode()
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _signature(encrypted: str, *, timestamp: str = "1700000000", nonce: str = "nonce-1") -> str:
    return hashlib.sha1("".join(sorted([TOKEN, timestamp, nonce, encrypted])).encode()).hexdigest()


def test_url_verification_returns_exact_plaintext_challenge() -> None:
    encrypted = _encrypt(b"callback-ok")

    challenge = verify_wecom_url(
        msg_signature=_signature(encrypted),
        timestamp="1700000000",
        nonce="nonce-1",
        encrypted=encrypted,
        callback_token=TOKEN,
        encoding_aes_key=AES_KEY,
        corp_id=CORP_ID,
    )

    assert challenge == b"callback-ok"


def test_text_callback_is_parsed_from_decrypted_xml() -> None:
    inner_xml = """<xml>
      <ToUserName><![CDATA[ww-test-corp]]></ToUserName>
      <AgentID>1000002</AgentID>
      <FromUserName><![CDATA[wecom-user-1]]></FromUserName>
      <MsgId>msg-10086</MsgId>
      <CreateTime>1700000000</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[我想咨询调休]]></Content>
    </xml>""".encode()
    encrypted = _encrypt(inner_xml)

    message = parse_wecom_callback(
        msg_signature=_signature(encrypted),
        timestamp="1700000000",
        nonce="nonce-1",
        encrypted=encrypted,
        callback_token=TOKEN,
        encoding_aes_key=AES_KEY,
        corp_id=CORP_ID,
        agent_id=AGENT_ID,
    )

    assert message.external_event_id == "msg:msg-10086"
    assert message.event_type == "text"
    assert message.external_user_id == "wecom-user-1"
    assert message.content == "我想咨询调休"
    assert message.occurred_at == "1700000000"


def test_callback_rejects_wrong_agent_before_any_event_write() -> None:
    inner_xml = b"""<xml><ToUserName>ww-test-corp</ToUserName><AgentID>1000003</AgentID>
    <FromUserName>wecom-user-1</FromUserName><MsgId>msg-10086</MsgId><CreateTime>1700000000</CreateTime>
    <MsgType>text</MsgType><Content>test</Content></xml>"""
    encrypted = _encrypt(inner_xml)

    with pytest.raises(WeComCallbackRejectedError, match="AgentID"):
        parse_wecom_callback(
            msg_signature=_signature(encrypted),
            timestamp="1700000000",
            nonce="nonce-1",
            encrypted=encrypted,
            callback_token=TOKEN,
            encoding_aes_key=AES_KEY,
            corp_id=CORP_ID,
            agent_id=AGENT_ID,
        )


def test_callback_rejects_invalid_signature_and_wrong_corp_id() -> None:
    encrypted = _encrypt(b"callback-ok", corp_id="ww-other-corp")

    with pytest.raises(WeComCallbackRejectedError, match="签名"):
        verify_wecom_url(
            msg_signature="wrong",
            timestamp="1700000000",
            nonce="nonce-1",
            encrypted=encrypted,
            callback_token=TOKEN,
            encoding_aes_key=AES_KEY,
            corp_id=CORP_ID,
        )
    with pytest.raises(WeComCallbackRejectedError, match="企业 ID"):
        verify_wecom_url(
            msg_signature=_signature(encrypted),
            timestamp="1700000000",
            nonce="nonce-1",
            encrypted=encrypted,
            callback_token=TOKEN,
            encoding_aes_key=AES_KEY,
            corp_id=CORP_ID,
        )
