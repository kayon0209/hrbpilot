"""HRBP AI Workbench — Tenant model.

No RLS on this table — Admin can access all tenants.
"""

from app.data.models.base import Base, UUIDPrimaryKey, TimestampMixin


class Tenant(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(nullable=False)
    config_json: Mapped[str | None] = mapped_column(default=None)  # JSON string for tenant-specific config

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} name={self.name}>"
