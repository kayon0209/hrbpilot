"""Sync runner dispatch contract — how run_connector_sync decides and behaves.

These tests mock the DB-backed primitives (_load_source, cursors, event log,
status write-back) and the HTTP client, so they run without live PostgreSQL.
The real DB-level idempotency is covered by the integration-marked
test_sync_engine.py / test_webhooks.py.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.connectors import runner
from app.shared.errors import AppError


def _source(**overrides: Any) -> SimpleNamespace:
    base = {
        "platform": "wecom",
        "content_types": json.dumps(["messages"]),
        "credential_encrypted": b"cipher",
        "oauth_app_id": "ww10086",
        "authorized_scope_json": {"chat_ids": ["chat-default"]},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _records() -> dict[str, list]:
    return {
        "fetches": [],
        "consumed": [],
        "cursors": [],
        "ok": [],
        "failed": [],
    }


def _stub(records: dict[str, list], monkeypatch: pytest.MonkeyPatch, *, load_row: Any = None) -> None:
    async def fake_load(tenant_id: str, source_id: str) -> Any:
        return load_row

    monkeypatch.setattr(runner, "_load_source", fake_load)

    class _FakeLockConn:
        async def execute(self, *a, **k):
            class _R:
                def scalar(self):
                    return True

            return _R()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeLockConn()

    monkeypatch.setattr(runner, "get_engine", lambda: _FakeEngine())

    async def fake_get_cursor(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(runner, "get_cursor", fake_get_cursor)

    async def fake_save_cursor(tenant_id, source_id, stream, cursor) -> None:
        records["cursors"].append((stream, cursor))

    monkeypatch.setattr(runner, "save_cursor", fake_save_cursor)

    async def fake_consume(*args, **kwargs) -> bool:
        records["consumed"].append(args)
        return True

    monkeypatch.setattr(runner, "consume_event", fake_consume)

    async def fake_ok(tenant_id, source_id) -> None:
        records["ok"].append(source_id)

    async def fake_failed(tenant_id, source_id, error) -> None:
        records["failed"].append((source_id, error))

    monkeypatch.setattr(runner, "mark_sync_ok", fake_ok)
    monkeypatch.setattr(runner, "mark_sync_failed", fake_failed)


async def _set_fetch(records: dict[str, list], pages: list[tuple[list[dict], str, bool]]) -> None:
    async def fake_fetch(tenant_id, source_id, credential, corpid, cursor=None, limit=100):
        records["fetches"].append((cursor, limit))
        return pages.pop(0) if pages else ([], "0", False)

    runner.clients.fetch_wecom_messages = fake_fetch  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_wecom_message_stream_pages_until_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _records()
    _stub(records, monkeypatch, load_row=_source())
    await _set_fetch(
        records,
        [
            ([{"external_id": "msg1", "chat": "chat-default"}, {"external_id": "msg2", "chat": "chat-default"}], "2", True),
            ([{"external_id": "msg3", "chat": "chat-default"}], "3", False),
        ],
    )

    stream = await runner.run_connector_sync("t1", "s1")

    assert stream == runner.WECOM_MESSAGE_STREAM
    assert records["ok"] == ["s1"]
    assert records["failed"] == []
    # resumed from no cursor, then advanced to the final seq
    assert records["cursors"] == [(runner.WECOM_MESSAGE_STREAM, "2"), (runner.WECOM_MESSAGE_STREAM, "3")]
    # every message consumed; empty chatdata external ids filtered
    consumed_ids = [args[2] for args in records["consumed"]]
    assert len(records["consumed"]) == 3
    assert all("msg" in eid for eid in consumed_ids)


@pytest.mark.asyncio
async def test_wecom_stream_drops_messages_outside_registered_chat_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records()
    _stub(
        records,
        monkeypatch,
        load_row=_source(authorized_scope_json={"chat_ids": ["chat-authorized"]}),
    )
    await _set_fetch(
        records,
        [
            (
                [
                    {"external_id": "allowed", "chat": "chat-authorized"},
                    {"external_id": "denied", "chat": "chat-outside-scope"},
                ],
                "2",
                False,
            )
        ],
    )

    await runner.run_connector_sync("t1", "s1")

    assert [args[2] for args in records["consumed"]] == ["allowed"]


@pytest.mark.asyncio
async def test_wecom_stream_marks_failed_on_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _records()
    _stub(records, monkeypatch, load_row=_source())

    # The provider client owns its own failure write-back then raises; the
    # runner must propagate (not fake ok) without double-marking.
    async def boom(*a, **k):
        records["fetches"].append("boom")
        await runner.mark_sync_failed("t1", "s1", "上游 502")
        raise AppError("上游 502", code="CONNECTOR_ERROR", status_code=502)

    runner.clients.fetch_wecom_messages = boom  # type: ignore[attr-defined]

    with pytest.raises(AppError):
        await runner.run_connector_sync("t1", "s1")

    # the honesty contract: failure recorded exactly once, never ok
    assert records["failed"] == [("s1", "上游 502")]
    assert records["ok"] == []


@pytest.mark.asyncio
async def test_unwired_stream_fails_closed_and_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _records()
    # feishu documents have no autosync client yet, fail closed
    _stub(records, monkeypatch, load_row=_source(platform="feishu", content_types=json.dumps(["documents"])))

    with pytest.raises(AppError) as exc_info:
        await runner.run_connector_sync("t1", "s1")

    assert exc_info.value.status_code == 501
    assert records["ok"] == []
    assert records["failed"] and "未接线" in records["failed"][0][1]


@pytest.mark.asyncio
async def test_missing_credential_or_app_id_never_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _records()
    _stub(records, monkeypatch, load_row=_source(credential_encrypted=None))
    await _set_fetch(records, [])

    with pytest.raises(AppError):
        await runner.run_connector_sync("t1", "s1")

    assert records["fetches"] == []
