"""Connector webhook ingress routes (CONN-07).

Provider callbacks are unauthenticated by definition, so every route verifies
the platform's signature BEFORE touching any state.  WeCom callbacks carry an
AES-encrypted body + SHA1 msg_signature; Feishu events carry an HMAC-SHA256
``X-Lark-Signature``.  Verified events go through the idempotent event log —
a redelivered event is counted and dropped, never re-processed.
"""

from fastapi import APIRouter, Request, Response

from app.connectors.sync import consume_event
from app.connectors.webhooks import WebhookRejected, ingest_feishu_event
from app.connectors.wecom_callback import parse_wecom_callback, verify_wecom_url
from app.scenarios.data_source.service import load_wecom_callback_config
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/connector-webhooks", tags=["connector-webhooks"])


def _wecom_signature_params(request: Request) -> tuple[str, str, str]:
    """Read the three signed URL parameters used by both WeCom callback phases."""
    return (
        request.query_params.get("msg_signature", ""),
        request.query_params.get("timestamp", ""),
        request.query_params.get("nonce", ""),
    )


@router.get("/wecom/{tenant_id}/{source_id}")
async def wecom_url_verification(tenant_id: str, source_id: str, request: Request) -> Response:
    """Complete WeCom's unauthenticated GET URL-verification handshake."""
    config = await load_wecom_callback_config(tenant_id, source_id)
    msg_signature, timestamp, nonce = _wecom_signature_params(request)
    encrypted = request.query_params.get("echostr", "")
    challenge = verify_wecom_url(
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
        callback_token=config.callback_token,
        encoding_aes_key=config.encoding_aes_key,
        corp_id=config.corp_id,
    )
    return Response(content=challenge, media_type="text/plain")


@router.post("/wecom/{tenant_id}/{source_id}")
async def wecom_callback(tenant_id: str, source_id: str, request: Request) -> Response:
    """Verify one encrypted XML message then atomically materialize the HR work item."""
    import xml.etree.ElementTree as ElementTree

    config = await load_wecom_callback_config(tenant_id, source_id)
    msg_signature, timestamp, nonce = _wecom_signature_params(request)
    try:
        encrypted = ElementTree.fromstring(await request.body()).findtext("Encrypt") or ""
    except ElementTree.ParseError as exc:
        from app.connectors.wecom_callback import WeComCallbackRejectedError

        raise WeComCallbackRejectedError("企业微信回调外层 XML 无效") from exc
    message = parse_wecom_callback(
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
        callback_token=config.callback_token,
        encoding_aes_key=config.encoding_aes_key,
        corp_id=config.corp_id,
        agent_id=config.agent_id,
    )
    await consume_event(
        tenant_id,
        source_id,
        message.external_event_id,
        message.event_type,
        {
            "sender": message.external_user_id,
            "content": message.content,
            "occurred_at": message.occurred_at,
        },
    )
    return Response(status_code=200)


@router.post("/feishu/{tenant_id}/{source_id}")
async def feishu_callback(tenant_id: str, source_id: str, request: Request) -> dict:
    """Verify and consume one Feishu event (v2 schema)."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    if not raw_body:
        raise WebhookRejected("飞书回调缺少事件内容")

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.id == source_id,
            )
        )
    if row is None:
        raise AppError("数据源不存在", code="NOT_FOUND", status_code=404)
    if not row.credential_encrypted:
        raise AppError("数据源未登记凭据，无法校验回调", code="CONFIG_ERROR", status_code=503)

    from app.connectors.credentials import decrypt_credential

    verification_token = decrypt_credential(tenant_id, row.credential_encrypted)

    authorization = request.headers.get("X-Lark-Signature", "")
    timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
    nonce = request.headers.get("X-Lark-Request-Nonce", "")
    if not authorization:
        raise WebhookRejected("缺少飞书事件签名")

    result = await ingest_feishu_event(
        tenant_id,
        source_id,
        authorization=authorization,
        verification_token=verification_token,
        timestamp=timestamp,
        nonce=nonce,
        body=raw_body,
    )
    if isinstance(result, dict) and "challenge" in result:
        return result
    if result is None:
        return {"replayed": True}
    return {"verified": True, "event_type": getattr(result, "event_type", "event")}
