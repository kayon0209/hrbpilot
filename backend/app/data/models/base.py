"""HRBP AI Workbench — SQLAlchemy ORM base and mixins.

All models inherit from Base. TenantMixin adds tenant_id column for RLS.
"""

import datetime
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class TimestampMixin:
    """Adds created_at and updated_at columns."""
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantMixin:
    """Adds tenant_id column for RLS multi-tenant isolation."""
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )


class UUIDPrimaryKey:
    """Uses UUID4 as primary key (String(36) for PostgreSQL compatibility)."""
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
