"""HRBP AI Workbench — smoke tests.

Verifies the app boots and core cost-control primitives work,
without requiring external services (DB / Milvus / Redis).
"""

import asyncio

from fastapi import FastAPI

from app.main import create_app
from app.shared.token_budget import record_token_usage


def test_create_app_boots():
    app = create_app()
    assert isinstance(app, FastAPI)
    paths = list(app.openapi().get("paths", {}).keys())
    assert len(paths) > 0
    assert "/api/health" in paths


def test_token_budget_records_usage():
    res = asyncio.run(record_token_usage("smoke-tenant", 1000, model="gpt-4o-mini"))
    assert res["within_budget"] is True
    assert res["tokens_used"] >= 1000
    assert res["alert_level"] in ("ok", "warning", "critical")
