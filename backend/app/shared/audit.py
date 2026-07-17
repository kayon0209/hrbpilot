"""HRBP AI Workbench — Audit logging helper.

Writes audit log entries (async, non-blocking). Uses structured logging.
"""

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
) -> None:
    """Write an audit log entry (fire-and-forget, non-blocking)."""
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
    )
