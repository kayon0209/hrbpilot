"""Explicit organisation scopes used for object-level authorization."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class OrgUnit(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "org_units"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("org_units.id"), nullable=True, index=True
    )


class ManagerOrgScope(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "manager_org_scopes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "manager_user_id", "org_unit_id", name="uq_manager_org_scope"),
    )

    manager_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    org_unit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("org_units.id"), nullable=False, index=True
    )
