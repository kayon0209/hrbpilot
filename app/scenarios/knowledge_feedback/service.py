"""Knowledge feedback candidate model + manager action center (spec §7.7).

Candidates are system suggestions from real usage signals. A candidate
NEVER auto-becomes a knowledge gap conclusion: only an hr_manager's explicit
confirm / assign / reject transitions its status, and every decision keeps
who decided and why.
"""

from __future__ import annotations

import json
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


async def _visible_scope(tenant_id: str, actor_id: str, actor_role: str) -> tuple[set[str], set[str]]:
    """Resolve readable users and their organisations before candidate queries."""
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.user import User

    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    if not visible_user_ids:
        return set(), set()
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        org_unit_rows = (
            await db.execute(
                select(User.org_unit_id).where(
                    User.tenant_id == tenant_id,
                    User.id.in_(visible_user_ids),
                    User.org_unit_id.is_not(None),
                )
            )
        ).scalars()
        org_unit_ids: set[str] = {str(org_unit_id) for org_unit_id in org_unit_rows}
    return visible_user_ids, org_unit_ids


def _candidate_scope_filter(model, visible_user_ids: set[str], org_unit_ids: set[str]):
    """Build a fail-closed predicate; legacy unscoped rows never match."""
    from sqlalchemy import false, or_

    clauses = []
    if org_unit_ids:
        clauses.append(model.org_unit_id.in_(org_unit_ids))
    if visible_user_ids:
        clauses.append(model.source_user_id.in_(visible_user_ids))
    return or_(*clauses) if clauses else false()


async def collect_candidates(tenant_id: str, actor_id: str, actor_role: str) -> list[FeedbackCandidate]:
    """Materialize candidates from real usage signals and merge stored decisions.

    Signals (spec §7.7):
      - 无证据问题: a user question whose assistant answer carried no citations
      - 低评价纠正: a question the user rated down
      - 高频未确认主题: the same question asked >= 3 times, still unresolved

    Pairing is per chat session: the user message is matched with the
    assistant answer that follows it, never crossed across sessions.
    """
    from sqlalchemy import select
    from sqlalchemy import text as sa_text
    from sqlalchemy.dialects import postgresql

    from app.data.database import get_session_factory
    from app.data.models.chat import ChatMessage, ChatSession
    from app.data.models.scenarios import KnowledgeFeedbackCandidate
    from app.data.models.user import User

    factory = get_session_factory()
    visible_user_ids, visible_org_unit_ids = await _visible_scope(tenant_id, actor_id, actor_role)
    if not visible_user_ids:
        return []

    signals: dict[tuple[str, str, str], dict] = {}

    async with factory() as db:
        db.info["tenant_id"] = tenant_id

        # Fetch user/assistant pairs per session, ordered, in one pass.
        rows = (
            await db.execute(
                select(ChatSession.id, ChatSession.user_id, User.org_unit_id, ChatMessage)
                .join(User, User.id == ChatSession.user_id)
                .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
                .where(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.user_id.in_(visible_user_ids),
                    ChatSession.scenario_id == "policy_qa",
                    ChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(ChatSession.created_at.asc(), ChatMessage.created_at.asc())
                .limit(500)
            )
        ).all()

        pairs: dict[str, tuple[str | None, str, list]] = {}
        for session_id, source_user_id, org_unit_id, message in rows:
            pair = pairs.setdefault(session_id, (org_unit_id, source_user_id, []))
            pair[2].append(message)

        for org_unit_id, source_user_id, messages in pairs.values():
            pending_question: str | None = None
            for message in messages:
                if message.role == "user":
                    pending_question = message.content
                elif message.role == "assistant" and pending_question:
                    has_citations = _has_citations(message.citations_json)
                    rated_down = message.feedback_rating == "down"
                    if (not has_citations) or rated_down:
                        scope_type = "org" if org_unit_id else "user"
                        scope_id = org_unit_id or source_user_id
                        key = (scope_type, scope_id, _question_key(pending_question))
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
                    .where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        _candidate_scope_filter(
                            KnowledgeFeedbackCandidate,
                            visible_user_ids,
                            visible_org_unit_ids,
                        ),
                    )
                    .order_by(KnowledgeFeedbackCandidate.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )

        by_key = {
            (
                "org" if row.org_unit_id else "user",
                row.org_unit_id or row.source_user_id,
                row.question_key or _question_key(row.question),
            ): row
            for row in stored
        }
        new_rows: list[KnowledgeFeedbackCandidate] = []
        updated_rows: list[KnowledgeFeedbackCandidate] = []
        inserted_keys: list[tuple[str, str, str]] = []
        for key, signal in signals.items():
            stored_row = by_key.get(key)
            if stored_row is not None:
                if stored_row.status == "open":
                    # Keep the materialized candidate in a live session so
                    # changes are committed, not made on a detached row.
                    stored_row.source_type = signal["source_type"]
                    stored_row.occurrences = signal["occurrences"]
                    stored_row.evidence_summary = signal.get("evidence_summary")
                    updated_rows.append(stored_row)
                continue
            row = KnowledgeFeedbackCandidate(
                tenant_id=tenant_id,
                org_unit_id=key[1] if key[0] == "org" else None,
                source_user_id=key[1] if key[0] == "user" else None,
                source_type=signal["source_type"],
                question=signal["question"][:400],
                question_key=key[2],
                occurrences=signal["occurrences"],
                evidence_summary=signal.get("evidence_summary"),
            )
            # Concurrent collection for the same scope+question races past the
            # in-Python by_key check; the partial unique indexes on
            # (tenant, org|user, question_key) are the real guard. Insert
            # atomically and let a conflict mean "someone else materialized it
            # first" instead of raising.
            insert_stmt = postgresql.insert(KnowledgeFeedbackCandidate).values(
                tenant_id=row.tenant_id,
                org_unit_id=row.org_unit_id,
                source_user_id=row.source_user_id,
                source_type=row.source_type,
                question=row.question,
                question_key=row.question_key,
                occurrences=row.occurrences,
                evidence_summary=row.evidence_summary,
            )
            # index_where must mirror the partial unique indexes from migration
            # 015 verbatim; PostgreSQL can only infer a partial index when the
            # ON CONFLICT predicate proves the row falls inside the predicate.
            if row.org_unit_id is not None:
                conflict_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=["tenant_id", "org_unit_id", "question_key"],
                    index_where=sa_text("org_unit_id IS NOT NULL AND question_key <> ''"),
                )
            else:
                conflict_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=["tenant_id", "source_user_id", "question_key"],
                    index_where=sa_text("source_user_id IS NOT NULL AND question_key <> ''"),
                )
            await db.execute(conflict_stmt)
            by_key[key] = row
            inserted_keys.append(key)
            new_rows.append(row)

        # Database defaults assign UUIDs only at flush time.  Serializing
        # before that point returns an ID which no decision endpoint can find.
        if new_rows or updated_rows:
            # The atomic INSERT above bypasses the session identity map, so a
            # racing insert that lost the unique-index race is not here yet —
            # re-read every inserted key from the database and replace the
            # optimistic in-memory projection with the persisted row.
            await db.flush()
            for key in inserted_keys:
                scope_type, scope_id, _ = key
                match_org = scope_type == "org"
                winner = (
                    (
                        await db.execute(
                            select(KnowledgeFeedbackCandidate).where(
                                KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                                (
                                    KnowledgeFeedbackCandidate.org_unit_id == scope_id
                                    if match_org
                                    else KnowledgeFeedbackCandidate.source_user_id == scope_id
                                ),
                                KnowledgeFeedbackCandidate.question_key == key[2],
                            )
                        )
                    )
                    .scalars()
                    .one()
                )
                by_key[key] = winner
            # ``updated_at`` is supplied by PostgreSQL's on-update default,
            # which SQLAlchemy expires after flush.  Reload it explicitly in
            # async context before creating the response projection.
            for row in updated_rows:
                await db.refresh(row)
        out = [_from_row(row) for row in by_key.values()]
        if new_rows or updated_rows:
            await db.commit()
            if new_rows:
                logger.info("knowledge_feedback_materialized", tenant_id=tenant_id, new_candidates=len(new_rows))

    out.sort(key=lambda c: (c.status != "open", -c.occurrences))
    return out[:50]


async def decide_candidate(tenant_id: str, user_id: str, user_role: str, body: DecideBody) -> FeedbackCandidate:
    """Apply a human decision — the ONLY way a candidate closes (spec §7.7).

    Atomicity: the decision is a conditional UPDATE ... WHERE status='open'.
    Whichever concurrent decide commits first wins; every other racer updates 0
    rows and gets an explicit 409.  A candidate that has already moved out of
    ``open`` (confirmed/assigned/rejected) can never be re-decided.
    """
    from typing import Any, cast

    from sqlalchemy import select, update
    from sqlalchemy.engine import CursorResult

    from app.data.database import get_session_factory
    from app.data.models.scenarios import KnowledgeFeedbackCandidate
    from app.shared.audit import append_security_audit_event

    if body.decision == "assign" and not body.assignee:
        raise AppError("指派需要填写负责人", code="VALIDATION_ERROR", status_code=400)

    status = {"confirm": "confirmed", "assign": "assigned", "reject": "rejected"}[body.decision]
    visible_user_ids, visible_org_unit_ids = await _visible_scope(tenant_id, user_id, user_role)
    handled_reason = (body.reason or "")[:1000] or None
    assignee = (body.assignee or "")[:200] or None
    now = datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        updated = cast(
            CursorResult[Any],
            await db.execute(
                update(KnowledgeFeedbackCandidate)
                .where(
                    KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                    KnowledgeFeedbackCandidate.id == body.candidate_id,
                    KnowledgeFeedbackCandidate.status == "open",
                    _candidate_scope_filter(
                        KnowledgeFeedbackCandidate,
                        visible_user_ids,
                        visible_org_unit_ids,
                    ),
                )
                .values(
                    status=status,
                    handled_by=user_id,
                    handled_reason=handled_reason,
                    assignee=assignee,
                    updated_at=now,
                )
            ),
        )
        if updated.rowcount != 1:
            await db.rollback()
            # Distinguish "not found / not visible / already decided" so the
            # caller gets a clear, actionable conflict, not a silent pass.
            current = (
                (
                    await db.execute(
                        select(KnowledgeFeedbackCandidate).where(
                            KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                            KnowledgeFeedbackCandidate.id == body.candidate_id,
                            _candidate_scope_filter(
                                KnowledgeFeedbackCandidate,
                                visible_user_ids,
                                visible_org_unit_ids,
                            ),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if current is None:
                raise NotFoundError("Feedback candidate", body.candidate_id)
            raise AppError(
                f"该候选当前状态为 {current.status or 'open'}，只能从 open 状态处理一次",
                code="STATE_CONFLICT",
                status_code=409,
            )

        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="knowledge_feedback.decided",
            object_type="knowledge_feedback_candidate",
            object_id=body.candidate_id,
            details={"decision": body.decision, "status": status},
        )
        await db.commit()

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
        decided = _from_row(row) if row is not None else None
        assert decided is not None
    logger.info(
        "knowledge_feedback_decided",
        tenant_id=tenant_id,
        decision=body.decision,
        status=status,
    )
    return decided


def _from_row(row) -> FeedbackCandidate:
    return FeedbackCandidate(
        candidate_id=row.id,
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


def _has_citations(citations_json: str | None) -> bool:
    """Return true only for a non-empty, contract-valid citation array."""
    if not citations_json:
        return False
    try:
        citations = json.loads(citations_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(citations, list) and bool(citations)


def _question_key(question: str) -> str:
    """Normalized dedup key (KNOW-01): whitespace-collapsed, lower-cased.

    A plain 120-char truncation could collide two long distinct questions and
    wrongly merge their candidates.  The key is the first 100 chars plus a
    stable 20-char SHA-256 suffix of the full text, so collisions are
    cryptographically negligible while the key stays within the 255-char
    column limit.
    """
    import hashlib

    normalized = " ".join(question.split()).lower()
    if len(normalized) <= 100:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"{normalized[:100]}:{digest}"
