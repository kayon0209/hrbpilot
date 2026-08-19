"""HRBP AI Workbench — Audit logging helper.

Writes audit log entries to PostgreSQL when available and always emits a
structured log event. The database row ID can be used as a durable message ID
for user-visible history and feedback links.
"""

from __future__ import annotations

import json

from app.shared.logger import get_logger

logger = get_logger(__name__)


async def write_audit_log(
    tenant_id: str,
    user_id: str,
    scenario_id: str,
    input_summary: str,
    output_summary: str,
    latency_ms: int,
    tokens_used: int | None = None,
    confidence: float = 0.0,
    guardrail_flags: dict | None = None,
    sources: list[dict] | None = None,
) -> str | None:
    """Write an audit log entry and return its database ID when persisted."""
    sources_trimmed = None
    if sources:
        sources_trimmed = [
            {
                "source": s.get("source", "unknown"),
                "section": s.get("section", "unknown"),
                "score": s.get("score", 0.0),
            }
            for s in sources[:5]
        ]

    audit_id: str | None = None
    try:
        from app.data.database import get_session_factory
        from app.data.models.infra import AuditLog

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            row = AuditLog(
                tenant_id=tenant_id,
                user_id=user_id,
                scenario_id=scenario_id,
                input_summary=input_summary[:2000],
                output_summary=output_summary[:2000],
                retrieved_docs_json=json.dumps(sources_trimmed, ensure_ascii=False) if sources_trimmed else None,
                llm_model_version=None,
                guardrail_result_json=json.dumps(guardrail_flags or {}, ensure_ascii=False),
                eval_score=confidence,
                response_latency_ms=latency_ms,
                token_consumption=tokens_used,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            audit_id = row.id
    except Exception as exc:
        logger.warning("audit_persist_failed", error=str(exc), scenario_id=scenario_id)

    logger.info(
        "audit_log",
        tenant_id=tenant_id,
        user_id=user_id,
        scenario_id=scenario_id,
        input_summary=input_summary[:200],
        output_summary=output_summary[:200],
        latency_ms=latency_ms,
        tokens_used=tokens_used,
        confidence=round(confidence, 4),
        guardrail_flags=guardrail_flags,
        retrieved_sources=sources_trimmed,
        audit_id=audit_id,
    )
    return audit_id
