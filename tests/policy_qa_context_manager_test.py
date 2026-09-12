"""Policy QA Context Manager tests (PR-02).

Locks the no-new-table Context Manager contract:

  - history is loaded only for the caller's tenant/user/session/scenario
  - history is clipped by max_messages and token budget
  - structured chat messages keep policy/task/history/evidence/current split
  - the policy QA route actually threads loaded history into the orchestrator
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models.base import Base
from app.data.models.chat import ChatMessage, ChatSession
from app.scenarios.policy_qa.context_manager import ContextManager, build_policy_qa_messages
from app.shared.errors import NotFoundError


@pytest.fixture()
def engine():
    return create_async_engine("sqlite+aiosqlite://")


@pytest.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(engine):
    from typing import cast

    from sqlalchemy import Table

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    cast(Table, ChatSession.__table__),
                    cast(Table, ChatMessage.__table__),
                ],
            )
        )


async def _seed_session(session_factory, *, tenant_id="t1", user_id="u1", scenario_id="policy_qa"):
    async with session_factory() as session:
        chat_session = ChatSession(tenant_id=tenant_id, user_id=user_id, scenario_id=scenario_id)
        session.add(chat_session)
        await session.flush()
        base = datetime.now(UTC)
        for i, (role, content) in enumerate(
            [
                ("user", "第一次提问：年假怎么休？"),
                ("assistant", "年假按制度办理。"),
                ("user", "如果没休完呢？"),
                ("assistant", "可以顺延。"),
            ]
        ):
            session.add(
                ChatMessage(
                    session_id=chat_session.id,
                    role=role,
                    content=content,
                    created_at=base + timedelta(seconds=i),
                    updated_at=base + timedelta(seconds=i),
                )
            )
        await session.commit()
        return chat_session.id


async def test_load_history_honors_tenant_user_session_scenario(session_factory):
    session_id = await _seed_session(session_factory)
    manager = ContextManager(max_messages=8, max_history_tokens=500)

    async with session_factory() as session:
        history = await manager.load_history(
            session,
            tenant_id="t1",
            user_id="u1",
            session_id=session_id,
            scenario_id="policy_qa",
        )

    assert history.session_id == session_id
    assert history.message_count == 4
    assert history.messages[0]["role"] == "user"
    assert history.messages[-1]["role"] == "assistant"
    assert history.truncated is False
    assert history.log_metadata()["summary_used"] is False


async def test_load_history_rejects_wrong_user(session_factory):
    session_id = await _seed_session(session_factory)
    manager = ContextManager()

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await manager.load_history(
                session,
                tenant_id="t1",
                user_id="other",
                session_id=session_id,
                scenario_id="policy_qa",
            )


async def test_history_is_clipped_oldest_first(session_factory):
    """Under token pressure the OLDEST messages are dropped; the newest
    turn — where the user's region/time/employee-type refinements live —
    must survive (P0-03 direction fix)."""
    session_id = await _seed_session(session_factory)
    manager = ContextManager(max_messages=8, max_history_tokens=6)

    async with session_factory() as session:
        history = await manager.load_history(
            session,
            tenant_id="t1",
            user_id="u1",
            session_id=session_id,
            scenario_id="policy_qa",
        )

    assert history.truncated is True
    assert history.message_count <= 2
    # Content-level: the kept messages are the NEWEST ones, in order.
    assert [m["content"] for m in history.messages] == ["如果没休完呢？", "可以顺延。"]


async def test_history_clip_fills_budget_with_newest_turns(session_factory):
    """A mid-size budget keeps as many NEWEST messages as fit; older turns
    are dropped first. The kept slice stays chronological for the model."""
    session_id = await _seed_session(session_factory)
    manager = ContextManager(max_messages=8, max_history_tokens=100)

    async with session_factory() as session:
        history = await manager.load_history(
            session,
            tenant_id="t1",
            user_id="u1",
            session_id=session_id,
            scenario_id="policy_qa",
        )

    contents = [m["content"] for m in history.messages]
    # The oldest turn may be dropped, but the newest pair never is.
    assert contents[-2:] == ["如果没休完呢？", "可以顺延。"]
    assert "第一次提问" not in contents or contents[0].startswith("第一次提问")
    # Chronological order preserved: user asks before assistant answers.
    assert history.messages[0]["role"] in ("user", "assistant")
    assert history.messages[-1]["role"] == "assistant"
    assert history.token_count <= 100


def test_build_policy_qa_messages_splits_system_evidence_and_query():
    prompt = Path("app/scenarios/policy_qa/prompts/policy_qa.txt").read_text(encoding="utf-8")
    messages = build_policy_qa_messages(
        prompt_template=prompt,
        query="年假能顺延吗？",
        evidence=[{"source": "员工手册.pdf", "section": "4.2", "content": "年假可顺延"}],
        history=[{"role": "user", "content": "上次问过年假"}, {"role": "assistant", "content": "可以顺延"}],
    )
    assert messages[0]["role"] == "system"
    assert "【系统规则】" in messages[0]["content"]
    assert any(msg["role"] == "assistant" for msg in messages)
    assert any(msg["role"] == "user" and msg["content"] == "年假能顺延吗？" for msg in messages)
    assert any(msg["role"] == "system" and "UNTRUSTED_EVIDENCE" in msg["content"] for msg in messages)


async def test_policy_qa_route_wires_history_into_orchestrator(monkeypatch, session_factory):
    """The route should load session history and pass it to the orchestrator,
    instead of claiming follow-up context that never reached generation."""
    from app.access.routes import policy_qa as policy_route

    session_id = await _seed_session(session_factory)

    class _Orch:
        def __init__(self):
            self.seen = {}

        async def execute(self, question, tenant_id, user_id, kb_id=None, *, history=None, history_meta=None):
            self.seen = {"question": question, "history": history, "history_meta": history_meta}
            return SimpleNamespace(
                answer="ok",
                citations=[],
                confidence=1.0,
                has_evidence=True,
                guardrail_flags={"history": history_meta},
                latency_ms=1,
                tokens_used=1,
                model_dump=lambda: {
                    "answer": "ok",
                    "citations": [],
                    "confidence": 1.0,
                    "has_evidence": True,
                    "guardrail_flags": {"history": history_meta},
                    "latency_ms": 1,
                    "tokens_used": 1,
                },
            )

    orch = _Orch()
    monkeypatch.setattr(policy_route, "orchestrator", orch)
    monkeypatch.setattr(policy_route, "require_auth", lambda f: f)
    monkeypatch.setattr(policy_route, "require_tenant_id", lambda request: "t1")

    async def _fake_kb(session, tenant_id, requested_kb_id):
        return SimpleNamespace(id="kb1")

    monkeypatch.setattr(policy_route, "_resolve_policy_kb", _fake_kb)

    async def _noop_save(*a, **k):
        return "msg-1"

    monkeypatch.setattr(policy_route, "_save_history_async", _noop_save)

    class _Request:
        def __init__(self) -> None:
            self.state = type("S", (), {"user_id": "u1"})()

    body = policy_route.AskRequest(question="年假能顺延吗？", kb_id="kb1", session_id=session_id, stream=False)
    from typing import Any, cast

    async with session_factory() as db:
        response = await policy_route.ask_question(body, cast(Any, _Request()), db)
    assert response["history_used"] is True
    assert response["history_message_count"] == 4
    assert orch.seen["history"] is not None
    assert orch.seen["history_meta"]["history_used"] is True
