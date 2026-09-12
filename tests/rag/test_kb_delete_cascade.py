"""§5.2: knowledge-base deletion must cascade beyond PostgreSQL.

The authoritative row is deleted in Postgres, but the bytes live in object
storage and the vectors live in Milvus. If either is left behind, the KB is
gone from the UI while its objects and embeddings quietly persist (and can
still be hydrated by a stale vector). This locks the cascade:

  Postgres rows → object storage objects → Milvus vectors

External cleanup is best-effort by design (a failed MinIO delete must not
resurrect the deletion), so the test also locks that: one store failing still
lets the other run and still returns success.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.access.routes import kb as kb_routes


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    """Returns the KB first, then the document list, mirroring delete_kb."""

    def __init__(self, kb, docs) -> None:
        self.kb = kb
        self.docs = docs
        self.events: list[str] = []
        self._results = [_Result(kb), _Result(docs)]

    async def execute(self, stmt):
        return self._results.pop(0) if self._results else _Result(None)

    async def delete(self, obj) -> None:
        self.events.append(f"delete:{type(obj).__name__}")

    async def commit(self) -> None:
        self.events.append("commit")


def _request(tenant_id: str = "t1") -> Request:
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    request.state.tenant_id = tenant_id
    request.state.user_id = "u1"
    # kb_management is an admin capability — the decorator reads user_role.
    request.state.user_role = "admin"
    return request


def _install_fakes(monkeypatch, session, *, milvus_raises: bool = False):
    deleted_keys: list[str] = []
    milvus_calls: list[str] = []
    # whether the authoritative commit had already happened when each external
    # cleanup ran — the route must delete/cleanup only after it commits
    committed_first: list[bool] = []

    class _ObjectStore:
        async def delete_async(self, key: str) -> None:
            deleted_keys.append(key)
            committed_first.append("commit" in session.events)

    class _MilvusStore:
        async def delete_by_kb_async(self, kb_id: str) -> int:
            milvus_calls.append(kb_id)
            committed_first.append("commit" in session.events)
            if milvus_raises:
                raise RuntimeError("milvus down")
            return 42

    monkeypatch.setattr(kb_routes, "ObjectStore", _ObjectStore)
    monkeypatch.setattr(kb_routes, "MilvusStore", _MilvusStore)
    return deleted_keys, milvus_calls, committed_first


@pytest.mark.asyncio
async def test_delete_kb_cascades_to_object_storage_and_vectors(monkeypatch):
    docs = [SimpleNamespace(s3_key="kb1/d1/a.pdf"), SimpleNamespace(s3_key="kb1/d2/b.pdf")]
    session = _Session(SimpleNamespace(id="kb1"), docs)
    deleted_keys, milvus_calls, committed_first = _install_fakes(monkeypatch, session)

    result = await kb_routes.delete_kb(
        kb_routes.DeleteKBBody(kb_id="kb1"), _request(), session
    )

    assert result["status"] == "deleted"
    assert result["documents"] == 2
    assert result["removed_vectors"] == 42
    # every stored object is removed, not just the first
    assert deleted_keys == ["kb1/d1/a.pdf", "kb1/d2/b.pdf"]
    # vectors are removed for the whole KB
    assert milvus_calls == ["kb1"]
    # the authoritative deletion is committed BEFORE external cleanup: if
    # cleanup half-fails, stale vectors cannot re-hydrate a live document
    assert committed_first and all(committed_first)


@pytest.mark.asyncio
async def test_delete_kb_still_succeeds_when_vector_cleanup_fails(monkeypatch):
    """Best-effort cleanup: a failing Milvus must not resurrect a deleted KB."""
    docs = [SimpleNamespace(s3_key="kb1/d1/a.pdf")]
    session = _Session(SimpleNamespace(id="kb1"), docs)
    deleted_keys, _, _ = _install_fakes(monkeypatch, session, milvus_raises=True)

    result = await kb_routes.delete_kb(
        kb_routes.DeleteKBBody(kb_id="kb1"), _request(), session
    )

    assert result["status"] == "deleted"
    assert result["removed_vectors"] == 0
    # object storage cleanup still happened despite the vector failure
    assert deleted_keys == ["kb1/d1/a.pdf"]


@pytest.mark.asyncio
async def test_delete_missing_kb_is_not_found(monkeypatch):
    session = _Session(None, [])
    _install_fakes(monkeypatch, session)

    with pytest.raises(kb_routes.NotFoundError):
        await kb_routes.delete_kb(
            kb_routes.DeleteKBBody(kb_id="missing"), _request(), session
        )
