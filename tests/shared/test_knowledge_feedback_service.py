"""Persistence regressions for knowledge-feedback materialization.

Needs a live PostgreSQL (chat/users/candidates tables), hence integration.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.data.database import get_session_factory
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.scenarios import KnowledgeFeedbackCandidate
from app.data.models.user import User
from app.scenarios.knowledge_feedback.service import collect_candidates

pytestmark = pytest.mark.integration


async def _seed_policy_pairs(
    tenant_id: str, question: str, citations_json: str | None, count: int = 1
) -> str:
    """Persist one user and ordered policy-QA question/answer pairs."""
    factory = get_session_factory()
    user_id = str(uuid4())
    now = datetime.now(UTC)
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="Knowledge feedback test user",
                email=f"{user_id}@example.test",
                hashed_password="not-a-real-password",
                role="hr_manager",
            )
        )
        # The chat session has a real foreign key to users; flush the parent
        # before constructing each child session.
        await db.flush()
        for index in range(count):
            chat_session = ChatSession(
                tenant_id=tenant_id,
                user_id=user_id,
                scenario_id="policy_qa",
                created_at=now + timedelta(seconds=index * 2),
            )
            db.add(chat_session)
            await db.flush()
            db.add_all(
                [
                    ChatMessage(
                        session_id=chat_session.id,
                        role="user",
                        content=question,
                        created_at=now + timedelta(seconds=index * 2),
                    ),
                    ChatMessage(
                        session_id=chat_session.id,
                        role="assistant",
                        content="暂未找到制度依据。",
                        citations_json=citations_json,
                        created_at=now + timedelta(seconds=index * 2 + 1),
                    ),
                ]
            )
        await db.commit()
    return user_id


async def _cleanup_tenant(tenant_id: str) -> None:
    """Remove only the ephemeral records created by this test tenant."""
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        session_ids = select(ChatSession.id).where(ChatSession.tenant_id == tenant_id)
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        await db.execute(delete(ChatSession).where(ChatSession.tenant_id == tenant_id))
        await db.execute(delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.commit()


async def test_collect_candidates_treats_empty_citations_json_as_no_evidence() -> None:
    tenant_id = str(uuid4())
    question = "补充公积金如何申请？"
    try:
        user_id = await _seed_policy_pairs(tenant_id, question, citations_json="[]")

        candidates = await collect_candidates(tenant_id, user_id, "hr_manager")

        candidate = next(item for item in candidates if item.question == question)
        assert candidate.source_type == "no_evidence"
    finally:
        await _cleanup_tenant(tenant_id)


async def test_collect_candidates_returns_the_persisted_candidate_id() -> None:
    tenant_id = str(uuid4())
    question = "外派期间的补贴标准是什么？"
    try:
        user_id = await _seed_policy_pairs(tenant_id, question, citations_json=None)

        candidates = await collect_candidates(tenant_id, user_id, "hr_manager")
        candidate = next(item for item in candidates if item.question == question)

        factory = get_session_factory()
        user_id = await _seed_policy_pairs(tenant_id, question, citations_json=None, count=3)
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            persisted_id = (
                await db.execute(
                    select(KnowledgeFeedbackCandidate.id).where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.question == question,
                    )
                )
            ).scalar_one()
        assert candidate.candidate_id == persisted_id
    finally:
        await _cleanup_tenant(tenant_id)


async def test_collect_candidates_persists_signal_changes_on_existing_open_candidate() -> None:
    tenant_id = str(uuid4())
    question = "调岗流程由谁审批？"
    candidate_id = str(uuid4())
    try:
        factory = get_session_factory()
        user_id = await _seed_policy_pairs(tenant_id, question, citations_json=None, count=3)
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                KnowledgeFeedbackCandidate(
                    id=candidate_id,
                    tenant_id=tenant_id,
                    source_user_id=user_id,
                    source_type="no_evidence",
                    question=question,
                    occurrences=1,
                )
            )
            await db.commit()

        await collect_candidates(tenant_id, user_id, "hr_manager")

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            persisted = await db.get(KnowledgeFeedbackCandidate, candidate_id)
        assert persisted is not None
        assert persisted.source_type == "repeated_theme"
        assert persisted.occurrences == 3
    finally:
        await _cleanup_tenant(tenant_id)
