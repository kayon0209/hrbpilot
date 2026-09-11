"""Admin audit history — durable security and governance events."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_session_factory
from app.data.models.infra import AuditLog
from app.data.models.user import User

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _load_json_or_text(raw: str | None, *, fallback_text: str = "", text_limit: int = 120) -> dict:
    """Tolerant audit payload decode (audit 2026-08-31 P1-1).

    ``audit_logs`` carries two writer generations: RAG pipeline rows store the
    raw user question / model answer as plain text, security events store
    structured JSON. A list page must never 500 because one legacy row is not
    valid JSON — degrade to a text summary instead of crashing the whole view.
    """
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"text": str(value)[:text_limit]}
    except (TypeError, ValueError):
        return {"text": (fallback_text or raw)[:text_limit]}


@router.get("/events")
@require_auth
@require_capability("audit_read")
async def list_audit_events(
    request: Request,
    object_id: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=200),
):
    tenant_id = require_tenant_id(request)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        statement = (
            select(AuditLog, User.name, User.email)
            .outerjoin(User, User.id == AuditLog.user_id)
            .where(AuditLog.tenant_id == tenant_id)
        )
        if object_id:
            statement = statement.where(AuditLog.input_summary.contains(f'"object_id": "{object_id}"'))
        rows = (await db.execute(statement.order_by(AuditLog.created_at.desc()).limit(limit))).all()

    events = []
    for row, actor_name, actor_email in rows:
        identity = _load_json_or_text(row.input_summary)
        details = _load_json_or_text(row.output_summary)
        events.append(
            {
                "event_id": row.id,
                "action": row.scenario_id,
                "actor_id": row.user_id,
                "actor_name": actor_name,
                "actor_email": actor_email,
                "object_type": identity.get("object_type"),
                "object_id": identity.get("object_id"),
                "input_summary": identity.get("text"),
                "details": details,
                "created_at": row.created_at.isoformat(),
            }
        )
    return {"events": events}
