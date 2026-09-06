"""Explicit organisation scopes used for object-level authorization.

OrgUnit.parent_id and ManagerOrgScope references are composite (tenant_id, id)
FKs (020) so no scope row can cross tenants at the database level.
"""

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class OrgUnit(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "org_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_org_units_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["org_units.tenant_id", "org_units.id"],
            name="fk_org_units_tenant_parent",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class ManagerOrgScope(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "manager_org_scopes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "manager_user_id", "org_unit_id", name="uq_manager_org_scope"),
        ForeignKeyConstraint(
            ["tenant_id", "manager_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_manager_org_scopes_tenant_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "org_unit_id"],
            ["org_units.tenant_id", "org_units.id"],
            name="fk_manager_org_scopes_tenant_org",
        ),
    )

    manager_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    org_unit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
