"""Data source service (Phase 5) — the unified ingestion contract.

Business language everywhere (spec §11): admins manage 数据接入 without
seeing connector/MCP vocabulary. Until a production KMS is configured the
API rejects credentials instead of pretending a reversible placeholder is
safe. Pause stops new syncs immediately; revoke also records when and why.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from app.shared.errors import NotFoundError, ValidationError
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


class CreateDataSourceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(..., pattern="^(wecom|feishu|dingtalk|wps365|exchange|oa|hris)$")
    purpose: str = Field(..., min_length=1, max_length=2000)  # 用途
    authorized_scope: str = Field(..., min_length=1, max_length=2000)  # 授权范围
    content_types: list[str] = Field(..., min_length=1)  # documents | messages | approvals | attachments
    data_destination: str = Field(..., min_length=1, max_length=2000)  # 数据去向
    credential: str | None = Field(None, max_length=10000)

    @field_validator("credential")
    @classmethod
    def reject_unmanaged_credential(cls, value: str | None) -> None:
        if value:
            raise ValueError("凭据安全存储尚未启用；请先完成企业密钥管理配置")
        return None


class UpdateDataSourceBody(BaseModel):
    purpose: str | None = Field(None, max_length=2000)
    authorized_scope: str | None = Field(None, max_length=2000)
    data_destination: str | None = Field(None, max_length=2000)


class DataSourceView(BaseModel):
    """Admin-facing view — no credential material, ever."""

    source_id: str
    name: str
    platform: str
    platform_label: str
    purpose: str
    authorized_scope: str
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
    updated_at: str | None


def _view(row) -> DataSourceView:
    sync_status = row.sync_status or "never_run"
    return DataSourceView(
        source_id=row.id,
        name=row.name,
        platform=row.platform,
        platform_label=PLATFORM_LABELS.get(row.platform, row.platform),
        purpose=row.purpose,
        authorized_scope=row.authorized_scope,
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
        updated_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    )


async def create_data_source(tenant_id: str, user_id: str, body: CreateDataSourceBody) -> DataSourceView:
    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    if body.platform == "exchange" and "messages" in body.content_types:
        raise ValidationError("企业邮箱不默认读取邮件正文；请仅选择文档与附件，或通过用户转交流程")

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = DataSource(
            tenant_id=tenant_id,
            name=body.name,
            platform=body.platform,
            purpose=body.purpose,
            authorized_scope=body.authorized_scope,
            content_types=json.dumps(body.content_types, ensure_ascii=False),
            data_destination=body.data_destination,
            created_by=user_id,
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
            details={"platform": body.platform},
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
            (
                await db.execute(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
            )
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
            (
                await db.execute(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
            )
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
    """撤销：立即停止新同步，记录时间与原因（spec §10.4 撤权可追踪）。"""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.data_source import DataSource
    from app.shared.audit import append_security_audit_event

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (
                await db.execute(
                    select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.id == source_id)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Data source", source_id)
        row.paused = True
        row.sync_status = "paused"
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = (reason or "管理员撤销")[:1000]
        row.credential_encrypted = None
        row.credential_ref = None
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="data_source.revoked",
            object_type="data_source",
            object_id=source_id,
            details={"reason": row.revoked_reason},
        )
        await db.commit()
        view = _view(row)
    logger.info("data_source_revoked", tenant_id=tenant_id, source_id=source_id, reason=reason)
    return view
