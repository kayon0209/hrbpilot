"""Admin-only tenant user and permission inventory."""

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_session_factory
from app.data.models.access_scope import OrgUnit
from app.data.models.user import User

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("")
@require_auth
@require_capability("user_admin")
async def list_users(request: Request):
    """Return the current tenant's user-role assignments without business data."""
    tenant_id = require_tenant_id(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(User, OrgUnit.name)
                .outerjoin(OrgUnit, OrgUnit.id == User.org_unit_id)
                .where(User.tenant_id == tenant_id)
                .order_by(User.name, User.email)
            )
        ).all()

    return {
        "users": [
            {
                "user_id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "org_unit": org_name,
            }
            for user, org_name in rows
        ]
    }
