"""Verify the audit tolerance fix end-to-end against real legacy rows.

Reads audit rows with non-JSON payloads (RAG pipeline plain-text rows,
tenant eval-runner) and asserts the tolerant decoder degrades them to text
instead of raising — the exact crash path an admin of a tenant with normal
QA traffic would hit.
"""
import asyncio

from app.access.routes.audit import _load_json_or_text


def test_real_legacy_rows_decode_without_crash():
    from sqlalchemy import text

    from app.data.database import get_engine

    async def fetch():
        eng = get_engine()
        async with eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT input_summary FROM audit_logs "
                        "WHERE scenario_id IN ('policy_qa','interview_digest','voice_insight','weekly_report') "
                        "ORDER BY created_at DESC LIMIT 50"
                    )
                )
            ).fetchall()
            return [r[0] for r in rows]

    rows = asyncio.run(fetch())
    if not rows:
        import pytest

        pytest.skip("no legacy audit rows in this environment")
    decoded = 0
    for raw in rows:
        value = _load_json_or_text(raw)  # must not raise for any legacy row
        assert isinstance(value, dict)
        decoded += 1
    assert decoded == len(rows)
