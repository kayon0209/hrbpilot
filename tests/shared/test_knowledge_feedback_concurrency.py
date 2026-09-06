"""PostgreSQL concurrency coverage for knowledge-feedback materialization.

Two managers polling ``collect_candidates`` for the same tenant, scope and
normalized question must materialize exactly one candidate. Without a
database-level uniqueness guarantee the in-Python ``by_key`` check is
check-then-insert and duplicates slip through under concurrent sessions.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.data.database import get_session_factory
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.scenarios import KnowledgeFeedbackCandidate
from app.data.models.user import User
from app.scenarios.knowledge_feedback.service import collect_candidates


async def _seed_scoped_question(tenant_id: str, user_id: str, org_unit_id: str | None, question: str) -> None:
    """Persist one ordered user/assistant policy-QA pair without citations."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        session = ChatSession(
            tenant_id=tenant_id,
            user_id=user_id,
            scenario_id="policy_qa",
            created_at=now,
        )
        db.add(session)
        await db.flush()
        db.add_all(
            [
                ChatMessage(
                    session_id=session.id,
                    role="user",
                    content=question,
                    created_at=now,
                ),
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content="暂未找到制度依据。",
                    citations_json=None,
                    created_at=now + timedelta(seconds=1),
                ),
            ]
        )
        await db.commit()


async def _cleanup_tenant(tenant_id: str) -> None:
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
async def test_concurrent_collect_materializes_one_candidate_per_scope() -> None:
    """Concurrent collection for the same scope+question must insert exactly one row."""
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    tenant_id = str(uuid4())
    user_id = str(uuid4())
    question = "并发物化的年假折算规则是什么？"
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                name="并发知识反馈用户",
                email=f"{user_id}@example.invalid",
                hashed_password="not-used-by-test",
                role="hr_manager",
            )
        )
        await db.commit()
    try:
        await _seed_scoped_question(tenant_id, user_id, None, question)

        barrier = asyncio.Barrier(2)

        async def collect() -> list:
            await barrier.wait()
            return await collect_candidates(tenant_id, user_id, "hr_manager")

        outcomes = await asyncio.gather(collect(), collect(), return_exceptions=True)
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert not failures, f"concurrent collect_candidates raised: {failures!r}"

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            total = (
                await db.execute(
                    select(func.count())
                    .select_from(KnowledgeFeedbackCandidate)
                    .where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.question == question,
                    )
                )
            ).scalar_one()
        assert total == 1, f"expected exactly one materialized candidate, found {total}"
    finally:
        await _cleanup_tenant(tenant_id)


@pytest.mark.asyncio
async def test_same_question_in_different_orgs_materializes_two_candidates() -> None:
    """Org-scoped aggregation keeps one candidate per organisation, never merged."""
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for the isolated PostgreSQL concurrency test")

    from app.data.models.access_scope import OrgUnit

    tenant_id = str(uuid4())
    manager_id = str(uuid4())
    employee_a_id, employee_b_id = str(uuid4()), str(uuid4())
    org_a_id, org_b_id = str(uuid4()), str(uuid4())
    question = "两支团队的调薪窗口分别是什么时候？"
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                OrgUnit(id=org_a_id, tenant_id=tenant_id, name="并发组织A"),
                OrgUnit(id=org_b_id, tenant_id=tenant_id, name="并发组织B"),
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="经理",
                    email=f"{manager_id}@example.invalid",
                    hashed_password="x",
                    role="hr_manager",
                ),
                User(
                    id=employee_a_id,
                    tenant_id=tenant_id,
                    name="A员工",
                    email=f"{employee_a_id}@example.invalid",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_a_id,
                ),
                User(
                    id=employee_b_id,
                    tenant_id=tenant_id,
                    name="B员工",
                    email=f"{employee_b_id}@example.invalid",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_b_id,
                ),
            ]
        )
        await db.commit()
    try:
        from app.data.models.access_scope import ManagerOrgScope

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add_all(
                [
                    ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a_id),
                    ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_b_id),
                ]
            )
            await db.commit()
        await _seed_scoped_question(tenant_id, employee_a_id, org_a_id, question)
        await _seed_scoped_question(tenant_id, employee_b_id, org_b_id, question)

        await collect_candidates(tenant_id, manager_id, "hr_manager")

        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            rows = (
                (
                    await db.execute(
                        select(KnowledgeFeedbackCandidate.org_unit_id).where(
                            KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                            KnowledgeFeedbackCandidate.question == question,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(rows) == sorted([org_a_id, org_b_id]), f"expected one candidate per org, got {rows!r}"
    finally:
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
            await db.commit()
        await _cleanup_tenant(tenant_id)
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
            await db.commit()
