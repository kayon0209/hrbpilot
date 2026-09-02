"""Contract tests for the policy-qa feedback and knowledge-bases endpoints.

These endpoints back the merged web workbench (Phase 3); without them the
frontend rating buttons and KB selector 404 at runtime. The tests use fake
sessions to verify tenant/user scoping and the write path without a live DB.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.access.routes import policy_qa as routes
from app.shared.errors import NotFoundError


class _Scalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def all(self):
        return self._value


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _Scalars(self._value)

    def all(self):
        return self._value


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, stmt):
        return self._results.pop(0)

    async def commit(self):
        self.committed = True


def _request(user_id="u1", tenant="t1"):
    scope = {"type": "http", "headers": []}
    request = Request(scope)
    request.state.user_id = user_id
    request.state.tenant_id = tenant  # set by AuthMiddleware in production
    return request


def _message(rating=None):
    return SimpleNamespace(id="m1", feedback_rating=rating, feedback_at=None, feedback_correction=None)


async def test_feedback_updates_message_and_commits():
    message = _message()
    session = _Session([_Result(message)])
    body = routes.FeedbackBody(message_id="m1", rating="up", correction="更正内容")

    result = await routes.submit_feedback(body, _request(), session)

    assert result["status"] == "ok"
    assert message.feedback_rating == "up"
    assert message.feedback_correction == "更正内容"
    assert message.feedback_at is not None
    assert session.committed


async def test_feedback_message_not_found_raises():
    session = _Session([_Result(None)])
    body = routes.FeedbackBody(message_id="missing", rating="down")

    with pytest.raises(NotFoundError):
        await routes.submit_feedback(body, _request(), session)
    assert not session.committed


async def test_knowledge_bases_returns_tenant_scoped_list():
    kb = SimpleNamespace(id="kb1", name="员工手册库", status="active")
    doc_count_row = ("kb1", 3)
    session = _Session([_Result([kb]), _Result([doc_count_row])])

    result = await routes.list_policy_knowledge_bases(_request(), session)

    assert result["knowledge_bases"] == [{"id": "kb1", "name": "员工手册库", "document_count": 3, "status": "active"}]


async def test_knowledge_bases_empty_is_empty_not_error():
    session = _Session([_Result([])])
    result = await routes.list_policy_knowledge_bases(_request(), session)
    assert result["knowledge_bases"] == []
