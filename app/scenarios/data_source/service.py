"""Data source service (Phase 5) — the unified ingestion contract.

Business language everywhere (spec §11): admins manage 数据接入 without
seeing connector/MCP vocabulary. Until a production KMS is configured the
API rejects credentials instead of pretending a reversible placeholder is
safe. Pause stops new syncs immediately; revoke also records when and why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from app.shared.errors import AppError, NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

PLATFORM_LABELS = {
    "wecom": "企业微信",
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "wps365": "WPS 365",
    "exchange": "企业邮箱（Microsoft 365）",
    "oa": "OA 系统",
    "hris": "HRIS 系统",
}

CERT_LEVEL_LABELS = {1: "准备接入", 2: "配置测试中", 3: "企业试用中", 4: "可使用"}

SYNC_LABELS = {
    "never_run": "尚未同步",
    "ok": "同步正常",
    "failed": "同步失败",
    "paused": "已暂停",
}

OAUTH_STATE_LABELS = {
    "none": "未授权",
    "pending": "授权进行中",
    "connected": "已授权",
    "expired": "授权已过期",
    "revoked": "已撤销授权",
}


class CreateDataSourceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(..., pattern="^(wecom|feishu|dingtalk|wps365|exchange|oa|hris)$")
    purpose: str = Field(..., min_length=1, max_length=2000)  # 用途
    authorized_scope: str = Field(..., min_length=1, max_length=2000)  # 授权范围
    # Canonical machine-readable boundaries.  Existing prose remains for
    # display, but a message sync requires a non-empty ``chat_ids`` list.
    authorized_scope_json: dict[str, list[str]] | None = None
    content_types: list[str] = Field(..., min_length=1)  # documents | messages | approvals | attachments
    event_route: str = Field("none", pattern="^(none|employee_request)$")
    data_destination: str = Field(..., min_length=1, max_length=2000)  # 数据去向
    credential: str | None = Field(None, max_length=10000)
    # OAuth / API credential material — stored only as tenant envelope
    # ciphertext (app.connectors.credentials); plaintext never persists.
    oauth_app_id: str | None = Field(None, max_length=128)
    oauth_redirect_uri: str | None = Field(None, max_length=500)


class UpdateDataSourceBody(BaseModel):
    purpose: str | None = Field(None, max_length=2000)
    authorized_scope: str | None = Field(None, max_length=2000)
    authorized_scope_json: dict[str, list[str]] | None = None
    data_destination: str | None = Field(None, max_length=2000)


class WeComCallbackConfigBody(BaseModel):
    """Write-only configuration for one WeCom self-built application callback."""

    corp_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    agent_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[0-9]+$")
    corp_secret: str = Field(..., min_length=1, max_length=2000)
    callback_token: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    encoding_aes_key: str = Field(..., min_length=43, max_length=43, pattern=r"^[A-Za-z0-9]{43}$")


@dataclass(frozen=True)
class WeComCallbackConfig:
    """Decrypted callback material for internal request verification only."""

    corp_id: str
    agent_id: str
    corp_secret: str
    callback_token: str
    encoding_aes_key: str


class DataSourceView(BaseModel):
    """Admin-facing view — no credential material, ever."""

    source_id: str
    name: str
    platform: str
    platform_label: str
    purpose: str
    authorized_scope: str
    authorized_scope_json: dict[str, list[str]] | None
    event_route: str
    content_types: list[str]
    data_destination: str
    certification_level: int
    certification_label: str
    sync_status: str
    sync_label: str
    last_sync_at: str | None
    next_sync_at: str | None
    last_error: str | None
    paused: bool
    revoked_at: str | None
    revoked_reason: str | None
    oauth_state: str
    oauth_state_label: str
    oauth_redirect_uri: str | None
    oauth_connected_at: str | None
    oauth_scopes: list[str]
    wecom_callback_configured: bool
    wecom_corp_id: str | None
    wecom_agent_id: str | None
    wecom_callback_path: str | None
    updated_at: str | None


def _wecom_callback_config_summary(row) -> tuple[bool, str | None, str | None]:
    encrypted = getattr(row, "wecom_callback_config_encrypted", None)
    if not encrypted:
        return False, None, None
    try:
        from cryptography.fernet import InvalidToken

        from app.connectors.credentials import decrypt_credential

        payload = json.loads(decrypt_credential(row.tenant_id, encrypted))
        corp_id = payload.get("corp_id")
        agent_id = payload.get("agent_id")
        if isinstance(corp_id, str) and isinstance(agent_id, str):
            return True, corp_id, agent_id
    except (InvalidToken, TypeError, ValueError):
        pass
    return False, None, None


def _view(row) -> DataSourceView:
    sync_status = row.sync_status or "never_run"
    wecom_configured, wecom_corp_id, wecom_agent_id = _wecom_callback_config_summary(row)
    return DataSourceView(
        source_id=row.id,
        name=row.name,
        platform=row.platform,
        platform_label=PLATFORM_LABELS.get(row.platform, row.platform),
        purpose=row.purpose,
        authorized_scope=row.authorized_scope,
        authorized_scope_json=getattr(row, "authorized_scope_json", None),
        event_route=getattr(row, "event_route", "none"),
        content_types=json.loads(row.content_types) if row.content_types else [],
        data_destination=row.data_destination,
        certification_level=row.certification_level or 1,
        certification_label=CERT_LEVEL_LABELS.get(row.certification_level or 1, "准备接入"),
        sync_status=sync_status,
        sync_label=SYNC_LABELS.get(sync_status, sync_status),
        last_sync_at=row.last_sync_at.isoformat() if getattr(row, "last_sync_at", None) else None,
        next_sync_at=row.next_sync_at.isoformat() if getattr(row, "next_sync_at", None) else None,
        last_error=row.last_error,
        paused=row.paused or False,
        revoked_at=row.revoked_at.isoformat() if getattr(row, "revoked_at", None) else None,
        revoked_reason=row.revoked_reason,
        oauth_state=row.oauth_state or "none",
        oauth_state_label=OAUTH_STATE_LABELS.get(row.oauth_state or "none", "未授权"),
        oauth_redirect_uri=getattr(row, "oauth_redirect_uri", None),
        oauth_connected_at=row.oauth_connected_at.isoformat() if getattr(row, "oauth_connected_at", None) else None,
        oauth_scopes=json.loads(row.oauth_scopes) if getattr(row, "oauth_scopes", None) else [],
        wecom_callback_configured=wecom_configured,
        wecom_corp_id=wecom_corp_id,
        wecom_agent_id=wecom_agent_id,
        wecom_callback_path=(
            f"/api/connector-webhooks/wecom/{row.tenant_id}/{row.id}"
            if row.platform == "wecom" and getattr(row, "event_route", "none") == "employee_request"
            else None
        ),
        updated_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    )


async def create_data_source(tenant_id: str, user_id: str, body: CreateDataSourceBody) -> DataSourceView:
    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    if body.platform == "exchange" and "messages" in body.content_types:
        raise ValidationError("企业邮箱不默认读取邮件正文；请仅选择文档与附件，或通过用户转交流程")
    if body.event_route == "employee_request":
        if body.platform not in {"wecom", "feishu"}:
            raise ValidationError("员工请求入口目前仅支持企业微信或飞书")
        if "messages" not in body.content_types:
            raise ValidationError("员工请求入口必须授权消息类型")

    # First-batch connectors (WeCom / Feishu) accept real credential
    # registration: with envelope encryption ready, credentials persist as
    # ciphertext and never appear in any API response or log. Other
    # platforms remain contract-only registration.
    from app.connectors.credentials import encrypt_credential
    from app.connectors.registry import SPECS

    credential_encrypted = None
    if body.credential:
        if body.platform not in SPECS:
            raise ValidationError("该渠道尚未开放凭据登记；请等待该连接器接入")
        if not body.oauth_app_id:
            raise ValidationError("该渠道凭据登记需要同时提供应用 ID")
        credential_encrypted = encrypt_credential(tenant_id, body.credential)

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = DataSource(
            tenant_id=tenant_id,
            name=body.name,
            platform=body.platform,
            purpose=body.purpose,
            authorized_scope=body.authorized_scope,
            authorized_scope_json=body.authorized_scope_json,
            event_route=body.event_route,
            content_types=json.dumps(body.content_types, ensure_ascii=False),
            data_destination=body.data_destination,
            created_by=user_id,
            credential_encrypted=credential_encrypted,
            oauth_state="none",
            oauth_app_id=body.oauth_app_id,
            oauth_redirect_uri=body.oauth_redirect_uri,
        )
        db.add(row)
        await db.flush()
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="data_source.created",
            object_type="data_source",
            object_id=row.id,
            details={"platform": body.platform, "credential_registered": credential_encrypted is not None},
        )
        await db.commit()
        view = _view(row)
    logger.info("data_source_created", tenant_id=tenant_id, platform=body.platform, source_id=row.id)
    return view


async def list_data_sources(tenant_id: str) -> list[DataSourceView]:
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(DataSource)
                    .where(DataSource.tenant_id == tenant_id)
                    .order_by(DataSource.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [_view(row) for row in rows]


async def configure_wecom_callback(
    tenant_id: str,
    actor_id: str,
    source_id: str,
    body: WeComCallbackConfigBody,
) -> dict[str, str | bool]:
    """Replace one WeCom callback bundle without ever returning its secrets."""
    from sqlalchemy import select

    from app.connectors.credentials import encrypt_credential
    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(
            select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id).with_for_update()
        )
        if row is None:
            raise NotFoundError("Data source", source_id)
        if row.platform != "wecom":
            raise ValidationError("仅企业微信接入可配置企微回调")
        if row.event_route != "employee_request":
            raise ValidationError("该数据源未配置为员工请求入口")
        if row.revoked_at is not None:
            raise ValidationError("已撤销的接入不能配置回调；请重新建立接入")

        payload = json.dumps(body.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        row.wecom_callback_config_encrypted = encrypt_credential(tenant_id, payload)
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.wecom_callback_configured",
            object_type="data_source",
            object_id=source_id,
            details={"configured": True, "corp_id": body.corp_id, "agent_id": body.agent_id},
        )
        await db.commit()
    return {
        "source_id": source_id,
        "configured": True,
        "corp_id": body.corp_id,
        "agent_id": body.agent_id,
        "callback_path": f"/api/connector-webhooks/wecom/{tenant_id}/{source_id}",
    }


async def load_wecom_callback_config(tenant_id: str, source_id: str) -> WeComCallbackConfig:
    """Load one callback bundle under tenant RLS without exposing it to an API view."""
    from cryptography.fernet import InvalidToken
    from sqlalchemy import select

    from app.connectors.credentials import decrypt_credential
    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
    if row is None:
        raise NotFoundError("Data source", source_id)
    if row.platform != "wecom" or row.event_route != "employee_request" or row.revoked_at is not None:
        raise AppError("数据源未完成企业微信员工请求入口配置", code="CONFIG_ERROR", status_code=503)
    if not row.wecom_callback_config_encrypted:
        raise AppError("数据源未完成企业微信回调配置", code="CONFIG_ERROR", status_code=503)
    try:
        payload = json.loads(decrypt_credential(tenant_id, row.wecom_callback_config_encrypted))
        config = WeComCallbackConfigBody.model_validate(payload)
    except (InvalidToken, TypeError, ValueError, PydanticValidationError) as exc:
        raise AppError("数据源企业微信回调配置无效", code="CONFIG_ERROR", status_code=503) from exc
    return WeComCallbackConfig(**config.model_dump())


async def bind_platform_identity(
    tenant_id: str,
    actor_id: str,
    source_id: str,
    external_user_id: str,
    user_id: str,
) -> dict[str, str]:
    """Upsert one explicitly verified platform-account binding.

    This administrative operation is deliberately source-scoped: a platform
    ID from one app/source cannot be reused to impersonate a user through
    another source, and database composite FKs enforce that all three objects
    belong to the same tenant.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from app.data.database import get_session_factory
    from app.data.models.connector import ConnectorIdentityBinding, ConnectorIntakeEvent
    from app.data.models.data_source import DataSource
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.shared.audit import append_security_audit_event

    external_user_id = external_user_id.strip()
    if not external_user_id:
        raise ValidationError("平台账号标识不能为空")

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        # This source-row lock serializes a new inbound event against the
        # administrator's binding.  Either the event sees the binding and
        # materializes immediately, or it is first persisted as pending and
        # then materialized below; there is no lost middle window.
        source = await db.scalar(
            select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id).with_for_update()
        )
        if source is None:
            raise NotFoundError("Data source", source_id)
        if source.event_route != "employee_request":
            raise ValidationError("该数据源未配置为员工请求入口")
        user = await db.scalar(select(User).where(User.tenant_id == tenant_id, User.id == user_id))
        if user is None:
            raise NotFoundError("User", user_id)
        if user.role != "employee":
            raise ValidationError("平台账号只能绑定到员工角色")

        await db.execute(
            insert(ConnectorIdentityBinding)
            .values(
                tenant_id=tenant_id,
                source_id=source_id,
                external_user_id=external_user_id,
                user_id=user_id,
                created_by=actor_id,
            )
            .on_conflict_do_update(
                constraint="uq_connector_identity_binding",
                set_={"user_id": user_id, "created_by": actor_id, "updated_at": datetime.now(UTC)},
            )
        )
        pending = list(
            (
                await db.execute(
                    select(ConnectorIntakeEvent)
                    .where(
                        ConnectorIntakeEvent.tenant_id == tenant_id,
                        ConnectorIntakeEvent.source_id == source_id,
                        ConnectorIntakeEvent.external_user_id == external_user_id,
                        ConnectorIntakeEvent.status == "pending_identity",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for intake in pending:
            request = EmployeeRequest(
                tenant_id=tenant_id,
                created_by=user_id,
                request_type="other",
                title="来自企业协作平台的员工请求",
                description=intake.description,
                status="submitted",
                next_step_for_employee="HR 会尽快处理；如需补充材料会在这里说明。",
                connector_source_id=source_id,
                connector_external_event_id=intake.external_event_id,
                external_sender_id=external_user_id,
            )
            db.add(request)
            await db.flush()
            intake.status = "materialized"
            intake.employee_request_id = request.id
            intake.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="connector_identity.bound",
            object_type="connector_identity_binding",
            object_id=f"{source_id}:{external_user_id}",
            details={
                "source_id": source_id,
                "external_user_id": external_user_id,
                "user_id": user_id,
                "materialized_pending_count": len(pending),
            },
        )
        await db.commit()
    return {"source_id": source_id, "external_user_id": external_user_id, "user_id": user_id}


async def pause_data_source(tenant_id: str, actor_id: str, source_id: str) -> DataSourceView:
    """暂停立即生效：不再发起任何新同步（spec §10.4）。"""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (await db.execute(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)))
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Data source", source_id)
        row.paused = True
        row.sync_status = "paused"
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.paused",
            object_type="data_source",
            object_id=source_id,
        )
        await db.commit()
        view = _view(row)
    logger.info("data_source_paused", tenant_id=tenant_id, source_id=source_id)
    return view


async def resume_data_source(tenant_id: str, actor_id: str, source_id: str) -> DataSourceView:
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (await db.execute(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)))
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Data source", source_id)
        if row.revoked_at:
            raise ValidationError("已撤销的接入不能恢复；请重新授权建立新接入")
        row.paused = False
        row.sync_status = "never_run" if row.sync_status == "paused" else row.sync_status
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.resumed",
            object_type="data_source",
            object_id=source_id,
        )
        await db.commit()
        return _view(row)


async def revoke_data_source(tenant_id: str, actor_id: str, source_id: str, reason: str) -> DataSourceView:
    """撤销：立即停止新同步，清理全部本地凭据/令牌，记录时间与原因（spec §10.4）。

    竞态安全：provider 侧网络调用在事务外执行，本地清理放在独立事务里无条件覆盖
    （置 revoked、清全部 token 密文、作废 nonce）——无论并发 callback 何时落盘，
    本地清理事务都会在后提交并胜出，因此 revoke 后 source 不可能被恢复为 connected。
    """
    from sqlalchemy import delete, select

    from app.connectors.credentials import decrypt_credential
    from app.connectors.oauth import revoke_provider_tokens
    from app.connectors.registry import spec_for
    from app.data.database import get_session_factory
    from app.data.models.connector import OAuthNonce
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    factory = get_session_factory()

    # --- Phase 1: read current token material into memory (short, no network). ---
    platform: str | None = None
    oauth_app_id: str | None = None
    app_secret: str | None = None
    refresh_plain: str | None = None
    has_oauth_tokens = False
    row_exists = True
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (await db.execute(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)))
            .scalars()
            .first()
        )
        if row is None:
            row_exists = False
        else:
            platform = row.platform
            oauth_app_id = row.oauth_app_id
            has_oauth_tokens = bool(row.oauth_encrypted_token or row.oauth_refresh_encrypted)
            if has_oauth_tokens or row.credential_encrypted:
                cred_enc = row.credential_encrypted
                if cred_enc:
                    app_secret = decrypt_credential(tenant_id, cred_enc)
                if row.oauth_refresh_encrypted:
                    refresh_plain = decrypt_credential(tenant_id, row.oauth_refresh_encrypted)
    if not row_exists:
        raise NotFoundError("Data source", source_id)

    # --- Phase 2: provider-side opt-out OUTSIDE any DB transaction/lock. ---
    # Provider outage is surfaced truthfully afterwards but never blocks the
    # local wipe, which is the security boundary that must hold.
    provider_revoked = True
    provider_note: str | None = None
    if has_oauth_tokens and platform and oauth_app_id and app_secret is not None:
        try:
            await revoke_provider_tokens(spec_for(platform), oauth_app_id, app_secret, refresh_plain)
        except Exception as exc:  # provider outage must not block local wipe
            provider_revoked = False
            provider_note = f"平台侧撤销失败：{type(exc).__name__}"[:200]
    elif has_oauth_tokens:
        provider_revoked = False
        provider_note = "平台侧撤销跳过：缺少应用 ID/密钥"

    # --- Phase 3: local wipe in its own transaction (unconditional override). ---
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (await db.execute(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)))
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Data source", source_id)

        row.paused = True
        row.sync_status = "paused"
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = (reason or "管理员撤销")[:1000]
        # Local credential and OAuth token wipe — the actual security boundary.
        row.credential_encrypted = None
        row.credential_ref = None
        row.wecom_callback_config_encrypted = None
        row.oauth_encrypted_token = None
        row.oauth_refresh_encrypted = None
        row.oauth_expires_at = None
        row.oauth_connected_at = None
        row.oauth_scopes = None
        row.oauth_user_id = None
        row.oauth_state = "revoked"
        # Invalidate every in-flight nonce for this source: a stale callback
        # can no longer complete the flow even with a previously issued state.
        await db.execute(
            delete(OAuthNonce).where(
                OAuthNonce.tenant_id == tenant_id,
                OAuthNonce.source_id == source_id,
            )
        )
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.revoked",
            object_type="data_source",
            object_id=source_id,
            details={
                "reason": row.revoked_reason,
                "provider_revoked": provider_revoked,
                "provider_note": provider_note,
            },
        )
        await db.commit()
        view = _view(row)
    logger.info("data_source_revoked", tenant_id=tenant_id, source_id=source_id, reason=reason)
    return view


async def start_oauth(tenant_id: str, actor_id: str, source_id: str, redirect_uri: str) -> dict:
    """Begin the platform consent flow for a registered data source.

    Generates a one-time, expiring, tenant/source/actor-bound CSRF nonce,
    persists only its SHA-256 fingerprint, and embeds the plaintext nonce in
    the provider consent URL as ``state``. The nonce must be presented back
    by the callback or the exchange is rejected.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.connectors.oauth import OAUTH_NONCE_TTL_MINUTES, authorize_url, generate_nonce, nonce_fingerprint
    from app.connectors.registry import spec_for
    from app.data.database import get_session_factory
    from app.data.models.connector import OAuthNonce
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event
    from app.shared.errors import NotFoundError

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        if row is None:
            raise NotFoundError("Data source", source_id)
        if row.revoked_at is not None:
            raise ValidationError("该数据源已撤销，不能发起授权")
        if not row.credential_encrypted or not row.oauth_app_id:
            raise ValidationError("请先在该数据源登记应用凭据（应用 ID 与密钥）后再发起授权")

        # redirect_uri allowlist (CONN-06): only an https URL that matches the
        # admin-registered callback is accepted; nothing else may be embedded
        # in the consent URL.  This prevents an open redirect / callback
        # hijack via the OAuth flow.
        from urllib.parse import urlparse

        parsed = urlparse(redirect_uri)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValidationError("回调地址必须是 https 完整 URL")
        registered = row.oauth_redirect_uri
        if registered and registered != redirect_uri:
            raise ValidationError("回调地址与登记的回调地址不一致")

        spec = spec_for(row.platform)
        nonce = generate_nonce()
        url = authorize_url(spec, row.oauth_app_id, redirect_uri, state=nonce)
        db.add(
            OAuthNonce(
                tenant_id=tenant_id,
                source_id=source_id,
                actor_id=actor_id,
                nonce_sha256=nonce_fingerprint(nonce),
                expires_at=datetime.now(UTC) + timedelta(minutes=OAUTH_NONCE_TTL_MINUTES),
            )
        )
        row.oauth_state = "pending"
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.oauth_started",
            object_type="data_source",
            object_id=row.id,
            details={"platform": row.platform},
        )
        await db.commit()
    return {"authorize_url": url}


async def complete_oauth(
    tenant_id: str,
    actor_id: str,
    source_id: str,
    code: str,
    state: str,
) -> DataSourceView:
    """Validate the CSRF nonce, then exchange the consent code.

    Order matters: the nonce is validated and consumed BEFORE any token
    exchange happens, so a missing/wrong/expired/already-consumed/cross-
    binding ``state`` can never reach the provider token endpoint.
    """
    from typing import Any, cast

    from sqlalchemy import select, update
    from sqlalchemy.engine import CursorResult

    from app.connectors.credentials import decrypt_credential
    from app.connectors.oauth import encrypt_token_bundle, exchange_code, nonce_fingerprint, nonce_matches
    from app.connectors.registry import spec_for
    from app.data.database import get_session_factory
    from app.data.models.connector import OAuthNonce
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event
    from app.shared.errors import NotFoundError

    factory = get_session_factory()

    # --- Phase 1: validate + consume the one-time nonce in a SHORT txn. ---
    # No network call happens here, so no long-lived DB lock is held during the
    # provider exchange.  The nonce is consumed atomically (replay-proof) and
    # the app credential is read into memory for the out-of-txn exchange.
    platform: str
    app_secret_encrypted: bytes | None = None
    app_id: str | None = None
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        if row is None:
            raise NotFoundError("Data source", source_id)
        if row.revoked_at is not None:
            # A source already revoked must not be reconnected through a stale
            # callback. (The phase-3 conditional update is the concurrency-safe
            # backstop; this is the fast-path friendly error for revoked state.)
            raise ValidationError("该数据源已撤销，不能通过回调重新授权")
        platform = row.platform
        app_secret_encrypted = row.credential_encrypted
        app_id = row.oauth_app_id

        if not state:
            raise ValidationError("OAuth 回调缺少 state，已拒绝")

        nonce_row = await db.scalar(
            select(OAuthNonce).where(
                OAuthNonce.tenant_id == tenant_id,
                OAuthNonce.source_id == source_id,
                OAuthNonce.actor_id == actor_id,
                OAuthNonce.nonce_sha256 == nonce_fingerprint(state),
            )
        )
        if nonce_row is None:
            raise ValidationError("OAuth state 无效或不属于当前请求，已拒绝")
        if not nonce_matches(nonce_row.nonce_sha256, state):
            raise ValidationError("OAuth state 无效，已拒绝")
        now = datetime.now(UTC)
        if nonce_row.expires_at is None or nonce_row.expires_at < now:
            # Consume the expired nonce so it cannot be resurrected later.
            await db.execute(
                update(OAuthNonce)
                .where(OAuthNonce.id == nonce_row.id, OAuthNonce.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            await db.commit()
            raise ValidationError("OAuth state 已过期，请重新发起授权")
        if nonce_row.consumed_at is not None:
            raise ValidationError("OAuth state 已被使用，已拒绝重放")

        # Atomic one-time consumption BEFORE the token exchange. If the
        # provider call fails, the nonce stays consumed — the flow must be
        # restarted from the top rather than replayed.
        consumed = cast(
            CursorResult[Any],
            await db.execute(
                update(OAuthNonce)
                .where(
                    OAuthNonce.id == nonce_row.id,
                    OAuthNonce.consumed_at.is_(None),
                    OAuthNonce.expires_at >= now,
                )
                .values(consumed_at=now)
            ),
        )
        if consumed.rowcount != 1:
            await db.rollback()
            raise ValidationError("OAuth state 已被使用，已拒绝重放")
        await db.commit()

    if app_secret_encrypted is None:
        raise ValidationError("数据源缺少应用凭据，无法完成授权")
    if not app_id:
        raise ValidationError("数据源缺少应用 ID，无法完成授权")

    # --- Phase 2: provider exchange OUTSIDE any DB transaction/lock. ---
    spec = spec_for(platform)
    app_secret = decrypt_credential(tenant_id, app_secret_encrypted)
    tokens = await exchange_code(spec, app_id, app_secret, code)
    bundle = encrypt_token_bundle(tenant_id, tokens)

    # --- Phase 3: conditional, atomic state transition. ---
    # Only a source that is still NOT revoked, NOT paused and still awaiting the
    # callback may become CONNECTED.  If revoke/pause committed while the
    # exchange was in flight, this UPDATE matches zero rows and the stale
    # callback is rejected — it can never resurrect a revoked source.
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        transition = cast(
            CursorResult[Any],
            await db.execute(
                update(DataSource)
                .where(
                    DataSource.tenant_id == tenant_id,
                    DataSource.id == source_id,
                    DataSource.oauth_state == "pending",
                    DataSource.revoked_at.is_(None),
                    DataSource.paused.is_(False),
                )
                .values(
                    oauth_state="connected",
                    oauth_encrypted_token=bundle["access"],
                    oauth_refresh_encrypted=bundle.get("refresh"),
                    oauth_expires_at=bundle["expires_at"],
                    oauth_connected_at=_dt.now(_UTC),
                    oauth_scopes=json.dumps(bundle.get("scopes") or []),
                    oauth_user_id=bundle.get("user_id"),
                )
            ),
        )
        if transition.rowcount != 1:
            await db.rollback()
            raise ValidationError("数据源已撤销或暂停，本次回调未生效；请重新发起授权")

        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.oauth_connected",
            object_type="data_source",
            object_id=source_id,
            details={"platform": platform, "scopes": bundle.get("scopes") or []},
        )
        await db.commit()

        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        if row is None:
            raise NotFoundError("Data source", source_id)
        view = _view(row)
    return view


async def trigger_sync(tenant_id: str, actor_id: str, source_id: str) -> DataSourceView:
    """Admin-triggered sync: honors pause, runs the wired stream, updates status honestly.

    Guards (revoke / pause / authorization) run before any provider call.  The
    actual pull, cursor advancement and status write-back happen in
    app.connectors.runner — so a source whose stream isn't wired surfaces an
    explicit infrastructure error instead of a fake ``sync_status=ok``.
    """
    from sqlalchemy import select

    from app.connectors.runner import run_connector_sync
    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event
    from app.shared.errors import NotFoundError

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        if row is None:
            raise NotFoundError("Data source", source_id)
        if row.revoked_at is not None:
            raise ValidationError("该数据源已撤销，不能同步")
        if row.paused:
            raise ValidationError("该数据源已暂停；请先恢复再同步")
        if row.oauth_state != "connected":
            raise ValidationError("尚未完成平台授权，无法同步")

        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.sync_triggered",
            object_type="data_source",
            object_id=row.id,
            details={"platform": row.platform},
        )
        await db.commit()

    try:
        stream = await run_connector_sync(tenant_id, source_id)
    except Exception as exc:  # runner already wrote sync_status=failed for provider errors
        if isinstance(exc, AppError):
            raise
        raise AppError(f"数据源同步失败：{type(exc).__name__}", code="CONNECTOR_ERROR", status_code=502) from exc

    logger.info("data_source_synced", tenant_id=tenant_id, source_id=source_id, stream=stream)
    # Re-read after the runner committed its own status write-back.
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await db.scalar(select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id))
        if row is None:
            raise NotFoundError("Data source", source_id)
        view = _view(row)
    return view
