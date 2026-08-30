"""Data source registry model (spec §五 Data Source, §10 数据接入).

Platform infra, not scenario data: admin-configured external channels.
Credential columns never serialize to API responses.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class DataSource(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "data_sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # wecom | feishu | dingtalk | wps365 | exchange | oa | hris
    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # 用途
    authorized_scope: Mapped[str] = mapped_column(Text, nullable=False)  # 授权范围
    content_types: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    data_destination: Mapped[str] = mapped_column(Text, nullable=False)  # 数据去向
    certification_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1..4 (spec §10.3)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="never_run")  # never_run | ok | failed | paused
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_reason: Mapped[str | None] = mapped_column(Text, default=None)
    credential_ref: Mapped[str | None] = mapped_column(String(500), default=None)  # KMS ref — never the secret
    credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)  # tenant-key encrypted
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
