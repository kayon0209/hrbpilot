"""Data source registry model (spec §五 Data Source, §10 数据接入).

Platform infra, not scenario data: admin-configured external channels.
Credential columns never serialize to API responses.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class DataSource(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "data_sources"
    __table_args__ = (
        # Must match migration 018 verbatim — alembic check compares against
        # this declared constraint, not the database.
        CheckConstraint(
            "oauth_state IN ('none', 'pending', 'connected', 'expired', 'revoked')",
            name="ck_data_sources_oauth_state",
        ),
        CheckConstraint(
            "event_route IN ('none', 'employee_request')",
            name="ck_data_sources_event_route",
        ),
        CheckConstraint(
            "wecom_callback_config_encrypted IS NULL OR platform = 'wecom'",
            name="ck_data_sources_wecom_callback_config",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_data_sources_tenant_id"),
    )

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
    # WeCom callback configuration is a distinct, tenant-envelope-encrypted
    # bundle.  It never reuses the application secret credential column.
    wecom_callback_config_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    # OAuth state (connector backbone, migration 018). Token ciphertext only —
    # plaintext access/refresh tokens never touch storage or any API response.
    oauth_state: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    oauth_app_id: Mapped[str | None] = mapped_column(String(128), default=None)
    # Admin-registered callback URL (CONN-06): oauth-start refuses to embed any
    # other redirect_uri in the consent URL.
    oauth_redirect_uri: Mapped[str | None] = mapped_column(String(500), default=None)
    oauth_encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    oauth_refresh_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    oauth_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    oauth_scopes: Mapped[str | None] = mapped_column(Text, default=None)
    oauth_user_id: Mapped[str | None] = mapped_column(String(128), default=None)
    credential_encrypted_v2: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    # Canonical machine-enforced source boundaries.  The existing
    # ``authorized_scope`` remains the administrator-facing description; a
    # missing structured scope is intentionally fail-closed for sync.
    authorized_scope_json: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # Explicit downstream contract for incoming platform events.  Free-text
    # purpose/data-destination fields must never select a business workflow.
    event_route: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
