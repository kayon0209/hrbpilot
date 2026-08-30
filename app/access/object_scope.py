"""Object-level visibility scopes for HR business records.

Tenant isolation is necessary but not sufficient.  Business records are
visible only to their explicit owner or to a manager with an explicit
organisation scope.  Unknown and platform-only roles fail closed.
"""

from __future__ import annotations


async def resolve_visible_user_ids(tenant_id: str, actor_id: str, actor_role: str) -> set[str]:
    """Return creator IDs the actor may read before a business query runs."""
    if actor_role == "hrbp":
        return {actor_id}
    if actor_role == "hr_manager":
        from sqlalchemy import select

        from app.data.database import get_session_factory
        from app.data.models.access_scope import ManagerOrgScope
        from app.data.models.user import User

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            org_ids = (
                (
                    await db.execute(
                        select(ManagerOrgScope.org_unit_id).where(
                            ManagerOrgScope.tenant_id == tenant_id,
                            ManagerOrgScope.manager_user_id == actor_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not org_ids:
                return {actor_id}
            user_ids = (
                (
                    await db.execute(
                        select(User.id).where(
                            User.tenant_id == tenant_id,
                            User.org_unit_id.in_(org_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {actor_id, *user_ids}
    return set()
