"""Durable, exact-bound ExecutionGrant issuance and atomic consumption."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.runtime import ExecutionGrant


class ExecutionGrantSpec(BaseModel):
    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    allowed_tool: str = Field(min_length=1)
    allowed_action: str = Field(min_length=1)
    normalized_params_hash: str | None = None
    policy_version: str = Field(min_length=1)
    permissions_version: str = Field(min_length=1)
    issued_for_run_id: str | None = None
    expires_at: datetime
    max_consumptions: int = Field(default=1, ge=1)


class ExecutionGrantRepository:
    """Database primitive; caller must re-evaluate ACL/Policy before issuing."""

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def issue(self, spec: ExecutionGrantSpec, now: datetime | None = None) -> ExecutionGrant:
        issued_at = now or datetime.now(UTC)
        if spec.expires_at <= issued_at:
            raise ValueError("execution grant expiry must be in the future")
        grant = ExecutionGrant(
            tenant_id=self._tenant_id,
            issued_at=issued_at,
            **spec.model_dump(),
        )
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def consume(
        self,
        grant_id: str,
        *,
        allowed_tool: str,
        allowed_action: str,
        object_type: str,
        object_id: str,
        normalized_params_hash: str | None,
        now: datetime | None = None,
    ) -> ExecutionGrant | None:
        """Atomically consume an exact grant; no caller may widen its binding."""
        claimed_at = now or datetime.now(UTC)
        updated = await self._session.execute(
            update(ExecutionGrant)
            .where(
                ExecutionGrant.id == grant_id,
                ExecutionGrant.tenant_id == self._tenant_id,
                ExecutionGrant.status == "active",
                ExecutionGrant.expires_at > claimed_at,
                ExecutionGrant.consumption_count < ExecutionGrant.max_consumptions,
                ExecutionGrant.allowed_tool == allowed_tool,
                ExecutionGrant.allowed_action == allowed_action,
                ExecutionGrant.object_type == object_type,
                ExecutionGrant.object_id == object_id,
                ExecutionGrant.normalized_params_hash == normalized_params_hash,
            )
            .values(
                consumption_count=ExecutionGrant.consumption_count + 1,
                status=case(
                    (ExecutionGrant.consumption_count + 1 >= ExecutionGrant.max_consumptions, "consumed"),
                    else_="active",
                ),
            )
            .returning(ExecutionGrant.id)
        )
        claimed_id = updated.scalar_one_or_none()
        if claimed_id is None:
            return None
        claimed_grant = await self._session.scalar(
            select(ExecutionGrant).where(
                ExecutionGrant.id == claimed_id,
                ExecutionGrant.tenant_id == self._tenant_id,
            )
        )
        return claimed_grant
