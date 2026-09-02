"""Knowledge-feedback candidates must stay inside explicit manager scope."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.infra import AuditLog
from app.data.models.scenarios import KnowledgeFeedbackCandidate
from app.data.models.user import User
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(tenant_id: str, manager_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": manager_id,
                "role": "hr_manager",
                "tenant_id": tenant_id,
                "email": f"{manager_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


async def _seed_scoped_questions(tenant_id: str) -> tuple[str, str, str, str]:
    manager_id, employee_a_id, employee_b_id = (str(uuid4()) for _ in range(3))
    org_a_id, org_b_id = str(uuid4()), str(uuid4())
    question_a = "华东团队的差旅补贴如何申请？"
    question_b = "华南团队的轮班津贴如何申请？"
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add_all(
            [
                OrgUnit(id=org_a_id, tenant_id=tenant_id, name="华东"),
                OrgUnit(id=org_b_id, tenant_id=tenant_id, name="华南"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                User(
                    id=manager_id,
                    tenant_id=tenant_id,
                    name="East manager",
                    email=f"{manager_id}@example.test",
                    hashed_password="x",
                    role="hr_manager",
                    org_unit_id=org_a_id,
                ),
                User(
                    id=employee_a_id,
                    tenant_id=tenant_id,
                    name="East employee",
                    email=f"{employee_a_id}@example.test",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_a_id,
                ),
                User(
                    id=employee_b_id,
                    tenant_id=tenant_id,
                    name="South employee",
                    email=f"{employee_b_id}@example.test",
                    hashed_password="x",
                    role="employee",
                    org_unit_id=org_b_id,
                ),
            ]
        )
        await db.flush()
        db.add(ManagerOrgScope(tenant_id=tenant_id, manager_user_id=manager_id, org_unit_id=org_a_id))
        for user_id, question in ((employee_a_id, question_a), (employee_b_id, question_b)):
            session = ChatSession(tenant_id=tenant_id, user_id=user_id, scenario_id="policy_qa")
            db.add(session)
            await db.flush()
            db.add_all(
                [
                    ChatMessage(session_id=session.id, role="user", content=question),
                    ChatMessage(
                        session_id=session.id,
                        role="assistant",
                        content="暂未找到制度依据。",
                        citations_json="[]",
                    ),
                ]
            )
        await db.commit()
    return manager_id, org_b_id, question_a, question_b


async def _seed_hidden_candidate(tenant_id: str, org_unit_id: str, question: str) -> str:
    candidate_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        db.add(
            KnowledgeFeedbackCandidate(
                id=candidate_id,
                tenant_id=tenant_id,
                org_unit_id=org_unit_id,
                source_type="no_evidence",
                question=question,
                occurrences=1,
                status="open",
            )
        )
        await db.commit()
    return candidate_id


async def _cleanup(tenant_id: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        session_ids = [
            row
            for row in (
                await db.execute(
                    ChatSession.__table__.select()
                    .with_only_columns(ChatSession.id)
                    .where(ChatSession.tenant_id == tenant_id)
                )
            ).scalars()
        ]
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.execute(
            delete(KnowledgeFeedbackCandidate).where(KnowledgeFeedbackCandidate.tenant_id == tenant_id)
        )
        if session_ids:
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        await db.execute(delete(ChatSession).where(ChatSession.tenant_id == tenant_id))
        await db.execute(delete(ManagerOrgScope).where(ManagerOrgScope.tenant_id == tenant_id))
        await db.execute(delete(User).where(User.tenant_id == tenant_id))
        await db.execute(delete(OrgUnit).where(OrgUnit.tenant_id == tenant_id))
        await db.commit()


def test_manager_candidate_list_excludes_questions_outside_explicit_org_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    manager_id, _org_b_id, visible_question, hidden_question = asyncio.run(
        _seed_scoped_questions(tenant_id)
    )
    try:
        response = client.get(
            "/api/knowledge-feedback/candidates",
            headers={"Authorization": f"Bearer {_token(tenant_id, manager_id)}"},
        )

        assert response.status_code == 200, response.text
        questions = {item["question"] for item in response.json()["candidates"]}
        assert visible_question in questions
        assert hidden_question not in questions
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_manager_cannot_decide_candidate_outside_explicit_org_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    manager_id, org_b_id, _visible_question, hidden_question = asyncio.run(
        _seed_scoped_questions(tenant_id)
    )
    candidate_id = asyncio.run(_seed_hidden_candidate(tenant_id, org_b_id, hidden_question))
    try:
        response = client.post(
            "/api/knowledge-feedback/candidates/decide",
            headers={"Authorization": f"Bearer {_token(tenant_id, manager_id)}"},
            json={"candidate_id": candidate_id, "decision": "reject", "reason": "不可见"},
        )

        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(tenant_id))


def test_work_summary_excludes_candidate_outside_explicit_org_scope(client: TestClient) -> None:
    tenant_id = str(uuid4())
    manager_id, org_b_id, _visible_question, hidden_question = asyncio.run(
        _seed_scoped_questions(tenant_id)
    )
    asyncio.run(_seed_hidden_candidate(tenant_id, org_b_id, hidden_question))
    try:
        response = client.get(
            "/api/work-summaries",
            headers={"Authorization": f"Bearer {_token(tenant_id, manager_id)}"},
        )

        assert response.status_code == 200, response.text
        serialized = response.text
        assert hidden_question not in serialized
    finally:
        asyncio.run(_cleanup(tenant_id))
