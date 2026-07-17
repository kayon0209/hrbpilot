"""HRBP AI Workbench — base repository with tenant-scoped queries.

All repositories inherit from this. Tenant context is enforced via RLS,
but we also provide helper methods for explicit tenant filtering.
"""

from typing import TypeVar, Generic, Type, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.base import Base, UUIDPrimaryKey, TenantMixin

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic async repository with CRUD operations."""

    def __init__(self, model: Type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, id: str) -> ModelType | None:
        """Get a single record by primary key."""
        result = await self.session.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, limit: int = 100, offset: int = 0) -> Sequence[ModelType]:
        """Get all records (tenant-scoped via RLS)."""
        result = await self.session.execute(
            select(self.model).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def create(self, obj: ModelType) -> ModelType:
        """Insert a new record."""
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, id: str, **kwargs) -> ModelType | None:
        """Update a record by ID with given fields."""
        obj = await self.get_by_id(id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def delete(self, id: str) -> bool:
        """Delete a record by ID."""
        obj = await self.get_by_id(id)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        return True
