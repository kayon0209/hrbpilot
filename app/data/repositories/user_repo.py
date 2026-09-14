"""HRBP AI Workbench — User repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.user import User
from app.data.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """User-specific queries beyond base CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str, *, tenant_id: str | None = None) -> User | None:
        """Find user by email (unique constraint), optionally scoped to one tenant.

        ``tenant_id`` 是**显式**过滤，不是可省的优化：AS 登录（``app/oauth/identity.py``）
        在租户上下文建立之前就要查 ``users``，而 RLS 的"owner 绕过"与"是否 FORCE"是
        部署级配置 —— 多租户登录的正确性不能押在它们的取值上。e2e 曾在真实进程里
        抓到这一点：同库同角色，仅 owner 不同，跨租户邮箱就能查出来。
        """
        stmt = select(User).where(User.email == email)
        if tenant_id is not None:
            stmt = stmt.where(User.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_tenant_and_role(self, tenant_id: str, role: str) -> list[User]:
        """Find users in a tenant with a specific role."""
        result = await self.session.execute(select(User).where(User.tenant_id == tenant_id, User.role == role))
        return list(result.scalars().all())
