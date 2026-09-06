"""Connector webhook ingress routes (CONN-07).

Provider callbacks are unauthenticated by definition, so every route verifies
the platform's signature BEFORE touching any state.  WeCom callbacks carry an
AES-encrypted body + SHA1 msg_signature; Feishu events carry an HMAC-SHA256
``X-Lark-Signature``.  Verified events go through the idempotent event log —
a redelivered event is counted and dropped, never re-processed.
"""

from fastapi import APIRouter, Request

from app.connectors.webhooks import (
    WebhookRejected,
    ingest_feishu_event,
    ingest_wecom_callback,
)
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/connector-webhooks", tags=["connector-webhooks"])


@router.post("/wecom/{tenant_id}/{source_id}")
async def wecom_callback(tenant_id: str, source_id: str, request: Request) -> dict:
    """Verify and consume one WeCom callback.

    The callback secret (token/AES key) is derived from the data source's
    registered credential at verify time; until a real test enterprise is
    connected this route still validates structure and idempotency.
    """
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource

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

    # The credential registration carries the app secret; the WeCom callback
    # token is the app secret used as the signature key in this integration.
    if not row.credential_encrypted:
        raise AppError("数据源未登记凭据，无法校验回调", code="CONFIG_ERROR", status_code=503)

    from app.connectors.credentials import decrypt_credential

    token = decrypt_credential(tenant_id, row.credential_encrypted)

    form = await request.form()
    encrypted = str(form.get("echostr", "") or form.get("Encrypt", "") or form.get("encrypt", ""))
    msg_signature = str(form.get("msg_signature", ""))
    timestamp = str(form.get("timestamp", ""))
    nonce = str(form.get("nonce", ""))
    if not encrypted or not msg_signature:
        raise WebhookRejected("缺少企业微信回调签名或加密内容")

    result = await ingest_wecom_callback(
        tenant_id,
        source_id,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
        token=token,
        aes_key_b64=token,
        corpid=row.oauth_app_id or "",
    )
    if result is None:
        return {"replayed": True}
    return {"verified": True, "event_type": result.event_type}


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
