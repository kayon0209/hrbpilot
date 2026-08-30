"""Knowledge feedback candidate model + manager action center (spec §7.7).

Candidates are system suggestions from real usage signals. A candidate
NEVER auto-becomes a knowledge gap conclusion: only an hr_manager's explicit
confirm / assign / reject transitions its status, and every decision keeps
who decided and why.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.shared.errors import AppError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

SOURCE_LABELS = {
    "no_evidence": "无证据问题",
    "negative_feedback": "低评价纠正",
    "repeated_theme": "高频未确认主题",
}


class FeedbackCandidate(BaseModel):
    candidate_id: str
    source_type: str  # no_evidence | negative_feedback | repeated_theme
    source_label: str
    question: str
    occurrences: int
    evidence_summary: str | None
    suggested_kb_id: str | None
    status: str  # open | confirmed | rejected | assigned
    handled_by: str | None
    handled_reason: str | None
    assignee: str | None
    updated_at: str | None


class DecideBody(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    decision: str = Field(..., pattern="^(confirm|assign|reject)$")
    reason: str | None = Field(None, max_length=1000)
    assignee: str | None = Field(None, max_length=200)


async def collect_candidates(tenant_id: str) -> list[FeedbackCandidate]:
    """Materialize candidates from real usage signals and merge stored decisions.

    Signals (spec §7.7):
      - 无证据问题: a user question whose assistant answer carried no citations
      - 低评价纠正: a question the user rated down
      - 高频未确认主题: the same question asked >= 3 times, still unresolved

    Pairing is per chat session: the user message is matched with the
    assistant answer that follows it, never crossed across sessions.
    """
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.chat import ChatMessage, ChatSession
    from app.data.models.scenarios import KnowledgeFeedbackCandidate

    factory = get_session_factory()

    signals: dict[str, dict] = {}  # question-key → signal info

    async with factory() as db:
        db.info["tenant_id"] = tenant_id

        # Fetch user/assistant pairs per session, ordered, in one pass.
        rows = (

                await db.execute(
                    select(ChatSession.id, ChatMessage)
                    .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
                    .where(
                        ChatSession.tenant_id == tenant_id,
                        ChatSession.scenario_id == "policy_qa",
                        ChatMessage.role.in_(("user", "assistant")),
                    )
                    .order_by(ChatSession.created_at.asc(), ChatMessage.created_at.asc())
                    .limit(500)
                )

        ).all()

        pairs: dict[str, list] = {}
        for _session_id, message in rows:
            pairs.setdefault(message.session_id, []).append(message)

        for _session_id, messages in pairs.items():
            pending_question: str | None = None
            for message in messages:
                if message.role == "user":
                    pending_question = message.content
                elif message.role == "assistant" and pending_question:
                    has_citations = bool(message.citations_json)
                    rated_down = message.feedback_rating == "down"
                    if (not has_citations) or rated_down:
                        key = _question_key(pending_question)
                        source = "negative_feedback" if rated_down else "no_evidence"
                        summary = (
                            (message.feedback_correction or "用户标记该回答需要改进。")[:200]
                            if rated_down
                            else "该问题在问答中未命中制度依据。"
                        )
                        if key not in signals:
                            signals[key] = {
                                "question": pending_question,
                                "source_type": source,
                                "occurrences": 1,
                                "evidence_summary": summary,
                            }
                        else:
                            signals[key]["occurrences"] += 1
                            if rated_down:
                                signals[key]["source_type"] = "negative_feedback"
                    pending_question = None

        # escalate repeated unresolved questions
        for signal in signals.values():
            if signal["occurrences"] >= 3:
                signal["source_type"] = "repeated_theme"

        stored = (
            (
                await db.execute(
                    select(KnowledgeFeedbackCandidate)
                    .where(KnowledgeFeedbackCandidate.tenant_id == tenant_id)
                    .order_by(KnowledgeFeedbackCandidate.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )

    by_key = {_question_key(row.question): row for row in stored}

    out: list[FeedbackCandidate] = []
    new_rows = []
    for key, signal in signals.items():
        stored_row = by_key.get(key)
        if stored_row is not None:
            if stored_row.status == "open" and stored_row.source_type != signal["source_type"]:
                stored_row.source_type = signal["source_type"]
                stored_row.occurrences = signal["occurrences"]
            out.append(_from_row(stored_row))
        else:
            row = KnowledgeFeedbackCandidate(
                tenant_id=tenant_id,
                source_type=signal["source_type"],
                question=signal["question"][:400],
                occurrences=signal["occurrences"],
                evidence_summary=signal.get("evidence_summary"),
            )
            new_rows.append(row)
            out.append(_from_row(row))

    for key, stored_row in by_key.items():
        if key not in signals:
            out.append(_from_row(stored_row))

    if new_rows:
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add_all(new_rows)
            await db.commit()
        logger.info("knowledge_feedback_materialized", tenant_id=tenant_id, new_candidates=len(new_rows))

    out.sort(key=lambda c: (c.status != "open", -c.occurrences))
    return out[:50]


async def decide_candidate(tenant_id: str, user_id: str, body: DecideBody) -> FeedbackCandidate:
    """Apply a human decision — the ONLY way a candidate closes (spec §7.7)."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import KnowledgeFeedbackCandidate
    from app.shared.audit import append_security_audit_event

    if body.decision == "assign" and not body.assignee:
        raise AppError("指派需要填写负责人", code="VALIDATION_ERROR", status_code=400)

    status = {"confirm": "confirmed", "assign": "assigned", "reject": "rejected"}[body.decision]
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (
                await db.execute(
                    select(KnowledgeFeedbackCandidate).where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.id == body.candidate_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Feedback candidate", body.candidate_id)
        row.status = status
        row.handled_by = user_id
        row.handled_reason = (body.reason or "")[:1000] or None
        row.assignee = (body.assignee or "")[:200] or None
        row.updated_at = datetime.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="knowledge_feedback.decided",
            object_type="knowledge_feedback_candidate",
            object_id=row.id,
            details={"decision": body.decision, "status": status},
        )
        await db.commit()
        decided = _from_row(row)
    logger.info(
        "knowledge_feedback_decided",
        tenant_id=tenant_id,
        decision=body.decision,
        status=status,
    )
    return decided


def _from_row(row) -> FeedbackCandidate:
    return FeedbackCandidate(
        candidate_id=row.id if hasattr(row, "id") and row.id else str(uuid.uuid4()),
        source_type=row.source_type,
        source_label=SOURCE_LABELS.get(row.source_type, row.source_type),
        question=row.question,
        occurrences=row.occurrences or 1,
        evidence_summary=row.evidence_summary or None,
        suggested_kb_id=row.suggested_kb_id,
        status=row.status or "open",
        handled_by=row.handled_by,
        handled_reason=row.handled_reason or None,
        assignee=row.assignee or None,
        updated_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    )


def _question_key(question: str) -> str:
    return " ".join(question.split())[:120].lower()
