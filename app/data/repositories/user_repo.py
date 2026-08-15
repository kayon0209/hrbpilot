"""HRBP AI Workbench — User repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.user import User
from app.data.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """User-specific queries beyond base CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        """Find user by email (unique constraint)."""
        result = await self.session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_tenant_and_role(self, tenant_id: str, role: str) -> list[User]:
        """Find users in a tenant with a specific role."""
        result = await self.session.execute(
            select(User).where(User.tenant_id == tenant_id, User.role == role)
        )
        return list(result.scalars().all())
