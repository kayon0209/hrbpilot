"""Real PostgreSQL checks for precise, one-time execution grants."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.approvals.grants import ExecutionGrantRepository, ExecutionGrantSpec
from app.data.database import make_tenant_session
from app.data.models.runtime import ExecutionGrant


@pytest.mark.asyncio
async def test_execution_grant_is_exact_bound_and_consumed_once() -> None:
    if not os.environ.get("HRBP_RUN_CONCURRENCY_TESTS"):
        pytest.skip("set HRBP_RUN_CONCURRENCY_TESTS=true for PostgreSQL execution-grant verification")

    tenant_id = str(uuid4())
    now = datetime.now(UTC)
    session = await make_tenant_session(tenant_id)
    try:
        repository = ExecutionGrantRepository(session, tenant_id)
        grant = await repository.issue(
            ExecutionGrantSpec(
                subject_type="user",
                subject_id=str(uuid4()),
                capability="case.write",
                object_type="hr_case",
                object_id=str(uuid4()),
                allowed_tool="case.assign_owner",
                allowed_action="assign",
                normalized_params_hash="1" * 64,
                policy_version="policy-1",
                permissions_version="permissions-1",
                expires_at=now + timedelta(minutes=5),
            ),
            now,
        )
        grant_id = grant.id
        object_id = grant.object_id
        await session.commit()

        consumed = await repository.consume(
            grant_id,
            allowed_tool="case.assign_owner",
            allowed_action="assign",
            object_type="hr_case",
            object_id=object_id,
            normalized_params_hash="1" * 64,
            now=now,
        )
        assert consumed is not None
        assert consumed.status == "consumed"
        await session.commit()

        assert (
            await repository.consume(
                grant_id,
                allowed_tool="case.assign_owner",
                allowed_action="assign",
                object_type="hr_case",
                object_id=object_id,
                normalized_params_hash="1" * 64,
                now=now,
            )
        ) is None
        assert (
            await repository.consume(
                grant_id,
                allowed_tool="case.assign_owner",
                allowed_action="assign",
                object_type="hr_case",
                object_id=object_id,
                normalized_params_hash="different",
                now=now,
            )
        ) is None
        await session.rollback()
        await session.execute(delete(ExecutionGrant).where(ExecutionGrant.id == grant_id))
        await session.commit()
    finally:
        await session.rollback()
        await session.close()
