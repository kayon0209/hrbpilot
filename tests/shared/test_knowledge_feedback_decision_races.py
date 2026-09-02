"""PostgreSQL concurrency coverage for single-winner candidate decisions.

A candidate may only be decided once from the ``open`` state. Whoever commits
first wins; every concurrent confirm / assign / reject on the same open
candidate updates 0 rows and is answered with an explicit conflict, and a
candidate already decided cannot be re-decided.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import get_session_factory
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.scenarios import KnowledgeFeedbackCandidate
from app.data.models.user import User
from app.scenarios.knowledge_feedback.service import DecideBody, decide_candidate
from app.shared.errors import AppError

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL concurrency verification")


async def _seed_open_candidate(tenant_id: str, manager_id: str, org_unit_id: str | None = None) -> str:
    """Insert one open candidate in the manager's user scope."""
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(User(id=manager_id, tenant_id=tenant_id, name="经理", email=f"{manager_id}@example.invalid",
                    hashed_password="x", role="hr_manager", org_unit_id=org_unit_id))
        await db.flush()
        row = KnowledgeFeedbackCandidate(
            tenant_id=tenant_id,
            source_user_id=manager_id,
            source_type="no_evidence",
            question="加班费与打卡规则是否冲突？",
            question_key="加班费与打卡规则是否冲突？",
            occurrences=2,
            status="open",
        )
        db.add(row)
        await db.flush()
        await db.commit()
        return row.id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        session_ids = select(ChatSession.id).where(ChatSession.tenant_id == tenant_id)
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        await db.execute(delete(ChatSession).where(ChatSession.tenant_id == tenant_id))
        await db.execute(delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


@pytest.mark.asyncio
async def test_confirm_vs_reject_exactly_one_wins() -> None:
    _require()
    tenant_id, manager_id = str(uuid4()), str(uuid4())
    candidate_id = await _seed_open_candidate(tenant_id, manager_id)
    try:
        async def decide(decision: str) -> str:
            try:
                result = await decide_candidate(
                    tenant_id, manager_id, "hr_manager",
                    DecideBody(**({"candidate_id": candidate_id, "decision": decision} | ({"assignee": "u2"} if decision == "assign" else {}))),
                )
                return result.status
            except AppError as exc:
                return f"{exc.status_code}:{exc.code}"

        outcomes = await asyncio.gather(*[decide(d) for d in ("confirm", "reject", "confirm")])
        winners = [o for o in outcomes if o in ("confirmed", "rejected", "assigned")]
        losers = [o for o in outcomes if o.startswith("409")]
        assert len(winners) == 1, f"expected exactly one winner, got {outcomes}"
        assert len(losers) == 2, f"expected two 409s, got {outcomes}"
    finally:
        await _cleanup(tenant_id)


@pytest.mark.asyncio
async def test_decided_candidate_cannot_be_re_decided() -> None:
    _require()
    tenant_id, manager_id = str(uuid4()), str(uuid4())
    candidate_id = await _seed_open_candidate(tenant_id, manager_id)
    try:
        first = await decide_candidate(
            tenant_id, manager_id, "hr_manager",
            DecideBody(candidate_id=candidate_id, decision="assign", assignee="u-9"),
        )
        assert first.status == "assigned"

        with pytest.raises(AppError) as exc_info:
            await decide_candidate(
                tenant_id, manager_id, "hr_manager",
                DecideBody(candidate_id=candidate_id, decision="reject"),
            )
        assert exc_info.value.status_code == 409
    finally:
        await _cleanup(tenant_id)
