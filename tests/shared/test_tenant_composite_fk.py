"""P0-05: composite (tenant_id, id) foreign keys reject cross-tenant binding.

Raw SQL is used deliberately: the database itself — not the service layer and
not RLS — must refuse a child row in tenant A referencing a parent row in
tenant B.  The composite FKs come from migration 020.
"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.data.database import make_tenant_session

pytestmark = pytest.mark.integration


def _require() -> None:
    if not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS") and not os.environ.get(
        "HRBP_RUN_CONCURRENCY_TESTS"
    ):
        pytest.skip("set HRBP_RUN_DB_SECURITY_TESTS=true for PostgreSQL FK verification")


async def _expect_fk_violation(session, sql: str) -> None:
    with pytest.raises(Exception) as exc_info:
        await session.execute(text(sql))
    import asyncpg

    cause = exc_info.value.__cause__
    assert isinstance(cause, asyncpg.exceptions.ForeignKeyViolationError) or (
        cause is not None and "ForeignKeyViolation" in type(cause).__name__
    ) or ("violates foreign key" in str(exc_info.value)), f"expected a foreign-key violation, got {exc_info.value}"


@pytest.mark.asyncio
async def test_cross_tenant_hr_case_child_is_rejected() -> None:
    _require()
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    user_a = str(uuid4())
    user_b = str(uuid4())
    sa = await make_tenant_session(tenant_a)
    sb = await make_tenant_session(tenant_b)
    case_b_id = ""
    try:
        # tenant A: one user + one case
        await sa.execute(
            text(
                "INSERT INTO users (id, tenant_id, name, email, hashed_password, role) "
                "VALUES (:id, :t, 'A', :email, 'x', 'hrbp')"
            ),
            {"id": user_a, "t": tenant_a, "email": f"{user_a}@a.invalid"},
        )
        await sa.commit()
        # tenant B: one user + one case
        await sb.execute(
            text(
                "INSERT INTO users (id, tenant_id, name, email, hashed_password, role) "
                "VALUES (:id, :t, 'B', :email, 'x', 'hrbp')"
            ),
            {"id": user_b, "t": tenant_b, "email": f"{user_b}@b.invalid"},
        )
        await sb.commit()

        case_b = await sb.execute(
            text(
                "INSERT INTO hr_cases (id, tenant_id, created_by, subject_ref, category, risk_level, status, title) "
                "VALUES (:id, :t, :u, 'SUBJ-B', 'overtime', 'LOW', 'NEW', 'B case') RETURNING id"
            ),
            {"id": str(uuid4()), "t": tenant_b, "u": user_b},
        )
        case_b_id = case_b.scalar_one()

        # A child (agent run) in tenant A referencing tenant B's case MUST fail.
        await _expect_fk_violation(
            sa,
            "INSERT INTO agent_runs (id, tenant_id, case_id, goal, status) "
            f"VALUES ('{uuid4()}', '{tenant_a}', '{case_b_id}', 'cross', 'RUNNING')",
        )
        await sa.rollback()
    finally:
        await sa.close()
        await sb.close()


@pytest.mark.asyncio
async def test_cross_tenant_oauth_nonce_referencing_foreign_source_is_rejected() -> None:
    _require()
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    user_a = str(uuid4())
    sa = await make_tenant_session(tenant_a)
    sb = await make_tenant_session(tenant_b)
    source_b_id = ""
    try:
        await sa.execute(
            text(
                "INSERT INTO users (id, tenant_id, name, email, hashed_password, role) "
                "VALUES (:id, :t, 'A', :email, 'x', 'admin')"
            ),
            {"id": user_a, "t": tenant_a, "email": f"{user_a}@a.invalid"},
        )
        await sa.commit()

        source_b = await sb.execute(
            text(
                "INSERT INTO data_sources (id, tenant_id, name, platform, purpose, authorized_scope, "
                "content_types, data_destination, created_by, oauth_state) "
                "VALUES (:id, :t, 'B source', 'wecom', 'p', 's', '[]', 'd', :u, 'none') RETURNING id"
            ),
            {"id": str(uuid4()), "t": tenant_b, "u": user_a},
        )
        source_b_id = source_b.scalar_one()
        await sb.commit()

        # An OAuth nonce in tenant A pointing at tenant B's source must fail.
        await _expect_fk_violation(
            sa,
            "INSERT INTO oauth_nonces (id, tenant_id, source_id, actor_id, nonce_sha256, expires_at) "
            f"VALUES ('{uuid4()}', '{tenant_a}', '{source_b_id}', '{user_a}', repeat('a',64), now() + interval '1 hour')",
        )
        await sa.rollback()
    finally:
        await sa.close()
        await sb.close()
