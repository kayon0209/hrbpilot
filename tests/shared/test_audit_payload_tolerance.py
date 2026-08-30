"""Regression tests for the tolerant audit payload decode (audit 2026-08-31 P1-1).

A tenant whose ``audit_logs`` contains legacy RAG pipeline rows (plain-text
input/output) must still render its admin audit page — the old unconditional
``json.loads`` turned the whole endpoint into a 500 once the first plain-text
row appeared in the calling tenant.
"""

from app.access.routes.audit import _load_json_or_text


def test_structured_security_event_decodes_normally():
    payload = '{"object_type": "data_source", "object_id": "abc-123"}'
    decoded = _load_json_or_text(payload)
    assert decoded == {"object_type": "data_source", "object_id": "abc-123"}


def test_legacy_plain_text_row_degrades_to_text_summary_not_crash():
    legacy = "员工张三反馈：入职八个月，试用期已通过但未收到转正通知"
    decoded = _load_json_or_text(legacy)
    assert "text" in decoded
    assert "张三" in decoded["text"]
    # No object identity is fabricated for text rows.
    assert "object_id" not in decoded


def test_null_and_empty_rows_decode_to_empty_dict():
    assert _load_json_or_text(None) == {}
    assert _load_json_or_text("") == {}


def test_non_dict_json_degrades_to_text():
    decoded = _load_json_or_text('["not", "an", "object"]')
    assert decoded == {"text": "['not', 'an', 'object']"}


def test_truncated_plain_text_is_limited():
    decoded = _load_json_or_text("很长的答案" * 500)
    assert len(decoded["text"]) <= 120


def test_events_payload_shape_includes_input_summary_for_legacy_rows():
    """The route must expose the degraded text so the UI can render it (P2-4)."""
    import inspect

    from app.access.routes.audit import list_audit_events

    source = inspect.getsource(list_audit_events)
    assert "input_summary" in source, "route response must carry the legacy text summary"
