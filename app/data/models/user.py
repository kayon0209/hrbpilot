"""HRBP AI Workbench — User model.

RBAC roles: Employee, HRBP, HRManager, Admin.
RLS enabled via tenant_id.  org_unit_id is a composite (tenant_id, org_unit_id)
FK (020) so a user can never be bound to another tenant's organisation.
"""

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class User(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "org_unit_id"],
            ["org_units.tenant_id", "org_units.id"],
            name="fk_users_tenant_org",
        ),
    )

    name: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(nullable=False, default="employee")  # employee | hrbp | hr_manager | admin
    org_unit_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"
