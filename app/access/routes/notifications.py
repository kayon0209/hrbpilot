"""Recipient-scoped in-app notification inbox."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.notification import InAppNotification
from app.shared.errors import NotFoundError

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _item(notification: InAppNotification) -> dict:
    """Expose only delivery metadata; case content remains behind case ACL."""
    return {
        "notification_id": notification.id,
        "case_id": notification.case_id,
        "template": notification.template,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


@router.get("")
@require_auth
@require_capability("notifications")
async def list_notifications(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """List the caller's own deliveries, never tenant-wide notification data."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "")
    rows = await session.scalars(
        select(InAppNotification)
        .where(
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.recipient_user_id == user_id,
        )
        .order_by(InAppNotification.read_at.asc().nulls_first(), InAppNotification.created_at.desc())
        .limit(limit)
    )
    return {"notifications": [_item(notification) for notification in rows]}


@router.post("/{notification_id}/read")
@require_auth
@require_capability("notifications")
async def mark_notification_read(
    notification_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Mark one owned delivery read; foreign IDs return 404 without disclosure."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "")
    notification = await session.scalar(
        select(InAppNotification)
        .where(
            InAppNotification.id == notification_id,
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.recipient_user_id == user_id,
        )
        .with_for_update()
    )
    if notification is None:
        raise NotFoundError("Notification", notification_id)
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.flush()
    return _item(notification)
