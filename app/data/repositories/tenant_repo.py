"""HRBP AI Workbench — Tenant repository.

No RLS — Admin can access all tenants.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.tenant import Tenant
from app.data.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Tenant, session)
