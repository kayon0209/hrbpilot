"""重新授权让旧连接让位（``_supersede_prior_families``）的行为测试。

背景：接入页告诉用户"想改权限，重新走一遍接入"。修复前，重新授权只是**加**
一条新链，旧链照常生效 —— 用户以为收窄了的权限其实没收窄。修复后：同一
(租户, 用户, 助手) 的旧 family 在新链签发的同一事务里被整体撤销（含连带作废
其待审批办理，与手动断开同一套语义）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.data.models.hr_case import ApprovalRequest
from app.data.models.oauth import OAuthRevokedToken, OAuthToken
from app.oauth import storage as oauth_storage
from app.oauth.tokens import REVOKED_REASON_SUPERSEDED, issue_tokens_for_authorization

TENANT = "tenant-a"
USER = "user-1"
CLIENT = "codex"


@pytest.fixture()
async def factory(sqlite_engine) -> async_sessionmaker[AsyncSession]:
    async with sqlite_engine.begin() as connection:
        await connection.run_sync(OAuthToken.__table__.create)
        await connection.run_sync(OAuthRevokedToken.__table__.create)
        # _cancel_bound_approvals 会 UPDATE 审批表；SQLite 不强制跨表外键，建本表即可。
        await connection.run_sync(ApprovalRequest.__table__.create)
    return async_sessionmaker(sqlite_engine, expire_on_commit=False)


async def _seed_family(
    factory: async_sessionmaker[AsyncSession],
    family_id: str,
    *,
    client_id: str = CLIENT,
    user_id: str = USER,
    tenant_id: str = TENANT,
    revoked: bool = False,
) -> None:
    async with factory() as session:
        session.add(
            OAuthToken(
                jti=f"jti-{family_id}",
                token_sha256=f"hash-{family_id}",
                family_id=family_id,
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=user_id,
                role="hrbp",
                auth_version=1,
                email=f"{user_id}@example.test",
                scope="hrb:policy:read",
                resource=f"{settings.public_base_url}/mcp",
                expires_at=datetime.now(UTC).replace(tzinfo=None),
                revoked_at=datetime.now(UTC).replace(tzinfo=None) if revoked else None,
            )
        )
        await session.commit()


async def _issue(factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(oauth_storage, "get_session_factory", lambda: factory)
    issued = await issue_tokens_for_authorization(
        client_id=CLIENT,
        tenant_id=TENANT,
        user_id=USER,
        role="hrbp",
        auth_version=1,
        email=f"{USER}@example.test",
        scope="hrb:policy:read",
        resource=f"{settings.public_base_url}/mcp",
    )
    assert issued.access_token
    return issued.refresh_token


async def _families(factory: async_sessionmaker[AsyncSession]) -> dict[str, str | None]:
    """family_id → 撤销原因（None = 仍有效）。同一 family 撤销原因一致。"""
    async with factory() as session:
        rows = (await session.execute(select(OAuthToken.family_id, OAuthToken.revoked_reason))).all()
    states: dict[str, str | None] = {}
    for family_id, reason in rows:
        if states.get(family_id) is None or reason is not None:
            states[family_id] = reason
    return states


async def test_reauthorization_revokes_prior_family_of_same_client(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_family(factory, "old-family")
    await _issue(factory, monkeypatch)

    states = await _families(factory)
    assert states["old-family"] == REVOKED_REASON_SUPERSEDED, "旧链必须被整体撤销"
    new_families = [f for f in states if f != "old-family"]
    assert len(new_families) == 1 and states[new_families[0]] is None


async def test_other_users_clients_and_tenants_are_untouched(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_family(factory, "same-trio")  # 应被撤销
    await _seed_family(factory, "other-user", user_id="user-2")
    await _seed_family(factory, "other-client", client_id="workbuddy")
    await _seed_family(factory, "other-tenant", tenant_id="tenant-b")
    await _seed_family(factory, "already-revoked", revoked=True)

    await _issue(factory, monkeypatch)

    states = await _families(factory)
    assert states["same-trio"] == REVOKED_REASON_SUPERSEDED
    for untouched in ("other-user", "other-client", "other-tenant"):
        assert states[untouched] is None, f"不属于同一 (租户,用户,助手) 的 {untouched} 不能被误伤"


async def test_superseded_family_lands_in_revocation_table(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """RS 手里只有 access token，靠撤销表拦截 —— 旧 family 必须进撤销表。"""
    await _seed_family(factory, "old-family")
    await _issue(factory, monkeypatch)

    async with factory() as session:
        row = await session.get(OAuthRevokedToken, ("family", "old-family"))
    assert row is not None
    assert row.reason == REVOKED_REASON_SUPERSEDED


async def test_no_prior_family_is_a_clean_first_install(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次接入没有旧链 —— 签发必须照常工作，不能被"让位"逻辑绊住。"""
    refresh = await _issue(factory, monkeypatch)
    assert refresh
    states = await _families(factory)
    assert len(states) == 1 and next(iter(states.values())) is None
