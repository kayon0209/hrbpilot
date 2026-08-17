"""HRBP AI Workbench — User model.

RBAC roles: Employee, HRBP, HRManager, Admin.
RLS enabled via tenant_id.
"""

from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class User(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(nullable=False, default="employee")  # employee | hrbp | hr_manager | admin

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"
