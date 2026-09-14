"""RFC 7662 内省端点。

这里用**真实的 SQL**（内存 SQLite）而不是打桩的会话：内省的价值有一半在于"它和 RS
用的是同一份撤销判据"，而那个判据就是一条 SQL（``jti`` 与 ``family`` 两个粒度）。
把会话换成假对象，恰好会把最该验证的东西验证掉。

不进入 lifespan：本文件测的是端点逻辑，不需要预注册客户端同步或密钥预热。
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.data.models.oauth import (
    REVOCATION_KIND_FAMILY,
    REVOCATION_KIND_JTI,
    OAuthRevokedToken,
    OAuthToken,
)
from app.oauth import tokens as oauth_tokens
from app.oauth.clients import ClientMetadata
from app.oauth.keys import active_signing_key
from app.oauth.main import create_oauth_app
from app.oauth.metadata import INTROSPECTION_PATH
from app.oauth.routes import introspect as introspect_route
from app.oauth.routes import token as token_route
from app.oauth.sessions import AsSession
from app.oauth.storage import hash_opaque_value
from app.oauth.tokens import ACCESS_TOKEN_TYPE, access_token_claims

_REGISTERED_CLIENT = "workbuddy"
_OTHER_CLIENT = "some-other-agent"


def _client_metadata(client_id: str) -> ClientMetadata:
    return ClientMetadata(
        client_id=client_id,
        client_name="Test Client",
        redirect_uris=("http://127.0.0.1/callback",),
        grant_types=("authorization_code", "refresh_token"),
        response_types=("code",),
        scopes=frozenset({"hrb:policy:read"}),
        registration_source="pre_registered",
    )


@pytest.fixture()
async def session_factory(
    sqlite_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> async_sessionmaker[AsyncSession]:
    """把 AS 的数据库会话换成一张内存表，只建内省会用到的两张。"""
    async with sqlite_engine.begin() as connection:
        await connection.run_sync(OAuthToken.__table__.create)
        await connection.run_sync(OAuthRevokedToken.__table__.create)
    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _oauth_session() -> AsyncIterator[AsyncSession]:
        session = factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    monkeypatch.setattr(oauth_tokens, "oauth_session", _oauth_session)

    async def _no_business_approvals(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(oauth_tokens, "_cancel_bound_approvals", _no_business_approvals)
    return factory


@pytest.fixture()
async def as_client(session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch):
    # This suite tests introspection semantics, not the shared anonymous
    # endpoint limiter. Disable the global buckets so prior tests/processes do
    # not make an isolated assertion depend on Redis or wall-clock time.
    monkeypatch.setattr(settings, "oauth_introspect_per_minute_per_ip", 0)
    monkeypatch.setattr(settings, "oauth_token_per_minute_per_client", 0)

    async def _resolve_client(client_id: str) -> ClientMetadata | None:
        if client_id in {_REGISTERED_CLIENT, _OTHER_CLIENT}:
            return _client_metadata(client_id)
        return None

    # 两个路由模块各自 ``from ... import resolve_client``，因此要各自替换一次。
    # （这本身不是问题：客户端解析仍然只有 registry 一个实现。）
    monkeypatch.setattr(introspect_route, "resolve_client", _resolve_client)
    monkeypatch.setattr(token_route, "resolve_client", _resolve_client)

    async def _current_identity(user_id: str, tenant_id: str) -> AsSession:
        return AsSession(
            user_id=user_id,
            tenant_id=tenant_id,
            role="hrbp",
            email="user-1@example.test",
            name="Test User",
            auth_version=1,
        )

    monkeypatch.setattr("app.oauth.identity.current_identity", _current_identity)
    transport = httpx.ASGITransport(app=create_oauth_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://as.example.test") as client:
        yield client


def _refresh_token_row(token: str, **overrides: object) -> OAuthToken:
    now = datetime.datetime.now(datetime.UTC)
    values: dict[str, object] = {
        "jti": str(uuid4()),
        "token_sha256": hash_opaque_value(token),
        "family_id": str(uuid4()),
        "client_id": _REGISTERED_CLIENT,
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "role": "hrbp",
        "email": "user-1@example.test",
        "scope": "hrb:policy:read",
        "resource": settings.mcp_resource_url,
        "expires_at": now + datetime.timedelta(days=30),
    }
    values.update(overrides)
    return OAuthToken(**values)


def _access_token(**overrides: object) -> str:
    now = datetime.datetime.now(datetime.UTC)
    values: dict[str, object] = {
        "client_id": _REGISTERED_CLIENT,
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "role": "hrbp",
        "email": "user-1@example.test",
        "scope": "hrb:policy:read",
        "resource": settings.mcp_resource_url,
        "family_id": str(uuid4()),
        "jti": str(uuid4()),
        "issued_at": now,
    }
    values.update(overrides)
    return active_signing_key().sign(access_token_claims(**values), token_type=ACCESS_TOKEN_TYPE)  # type: ignore[arg-type]


async def _post(client: httpx.AsyncClient, *, token: str, client_id: str = _REGISTERED_CLIENT) -> httpx.Response:
    return await client.post(INTROSPECTION_PATH, data={"token": token, "client_id": client_id})


# --------------------------------------------------------------------------- #
# 端点本身的要求
# --------------------------------------------------------------------------- #


async def test_unknown_client_is_refused(as_client: httpx.AsyncClient) -> None:
    response = await _post(as_client, token="whatever", client_id="never-registered")

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_missing_client_id_is_refused(as_client: httpx.AsyncClient) -> None:
    response = await as_client.post(INTROSPECTION_PATH, data={"token": "whatever"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_unknown_token_is_inactive_not_an_error(as_client: httpx.AsyncClient) -> None:
    """未知令牌回 ``active: false`` 而不是 400 —— 否则这里成了"值像不像令牌"的探测器。"""
    response = await _post(as_client, token="not-a-real-token")

    assert response.status_code == 200
    assert response.json() == {"active": False}


async def test_responses_must_not_be_cached(as_client: httpx.AsyncClient) -> None:
    """内省响应会泄漏"某个时刻这把令牌是否有效"，属 RFC 6749 §5.1 同类的禁止缓存。"""
    response = await _post(as_client, token="not-a-real-token")

    assert response.headers["Cache-Control"] == "no-store"


async def test_absurdly_long_token_is_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    response = await _post(as_client, token="x" * 5000)

    assert response.status_code == 200
    assert response.json() == {"active": False}


# --------------------------------------------------------------------------- #
# refresh token
# --------------------------------------------------------------------------- #


async def test_an_active_refresh_token_is_reported(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    raw = "refresh-token-value-1"
    async with session_factory() as session:
        session.add(_refresh_token_row(raw))
        await session.commit()

    body = (await _post(as_client, token=raw)).json()

    assert body["active"] is True
    assert body["client_id"] == _REGISTERED_CLIENT
    assert body["sub"] == "user-1"
    assert body["scope"] == "hrb:policy:read"
    assert body["token_type"] == "refresh_token"
    # 刻意的取舍：不回 username / email。它们会被客户端写进日志。
    assert "username" not in body
    assert "email" not in body


async def test_a_used_refresh_token_is_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """已经换过一次令牌的那把等于被取代了 —— 它不该还显得可用。"""
    raw = "refresh-token-value-2"
    async with session_factory() as session:
        session.add(_refresh_token_row(raw, used_at=datetime.datetime.now(datetime.UTC)))
        await session.commit()

    assert (await _post(as_client, token=raw)).json() == {"active": False}


async def test_a_revoked_refresh_token_is_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    raw = "refresh-token-value-3"
    async with session_factory() as session:
        session.add(
            _refresh_token_row(raw, revoked_at=datetime.datetime.now(datetime.UTC), revoked_reason="user_revoked")
        )
        await session.commit()

    assert (await _post(as_client, token=raw)).json() == {"active": False}


async def test_an_expired_refresh_token_is_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    raw = "refresh-token-value-4"
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    async with session_factory() as session:
        session.add(_refresh_token_row(raw, expires_at=past))
        await session.commit()

    assert (await _post(as_client, token=raw)).json() == {"active": False}


async def test_another_clients_refresh_token_looks_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """别人的令牌一律表现为"无效"，而不是"不是你的"。后者是一个存在性预言机。"""
    raw = "refresh-token-value-5"
    async with session_factory() as session:
        session.add(_refresh_token_row(raw, client_id=_OTHER_CLIENT))
        await session.commit()

    assert (await _post(as_client, token=raw)).json() == {"active": False}


# --------------------------------------------------------------------------- #
# access token
# --------------------------------------------------------------------------- #


async def test_an_active_access_token_is_reported(as_client: httpx.AsyncClient) -> None:
    body = (await _post(as_client, token=_access_token())).json()

    assert body["active"] is True
    assert body["token_type"] == "Bearer"
    assert body["iss"] == settings.oauth_issuer
    assert body["scope"] == "hrb:policy:read"
    assert isinstance(body["exp"], int)


async def test_a_revoked_access_token_is_inactive_by_jti(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    jti = str(uuid4())
    token = _access_token(jti=jti)
    async with session_factory() as session:
        session.add(
            OAuthRevokedToken(
                kind=REVOCATION_KIND_JTI,
                value=jti,
                expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=15),
                reason="user_revoked",
                tenant_id="tenant-a",
            )
        )
        await session.commit()

    assert (await _post(as_client, token=token)).json() == {"active": False}


async def test_a_revoked_family_makes_every_access_token_in_it_inactive(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """``family`` 粒度必须生效。

    这是撤销链路里最容易漏掉的一半：用户撤销的是"这次授权"，而 RS/内省手里只有
    access token，能对上号的只有 ``family_id``。只查 ``jti`` 会让撤销**静默失效一半**
    —— refresh token 立刻不可用，而 access token 继续有效到自然过期。
    """
    family_id = str(uuid4())
    token = _access_token(family_id=family_id)
    async with session_factory() as session:
        session.add(
            OAuthRevokedToken(
                kind=REVOCATION_KIND_FAMILY,
                value=family_id,
                expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=15),
                reason="refresh_token_reuse_detected",
                tenant_id="tenant-a",
            )
        )
        await session.commit()

    assert (await _post(as_client, token=token)).json() == {"active": False}


async def test_another_clients_access_token_looks_inactive(as_client: httpx.AsyncClient) -> None:
    token = _access_token(client_id=_OTHER_CLIENT)

    assert (await _post(as_client, token=token)).json() == {"active": False}


async def test_a_token_for_another_resource_looks_inactive(as_client: httpx.AsyncClient) -> None:
    """内省与 RS 用同一套 audience 校验：为别的资源签的令牌在这里也不"有效"。"""
    token = _access_token(resource="https://other.example.test/mcp")

    assert (await _post(as_client, token=token)).json() == {"active": False}


async def test_role_change_makes_an_existing_access_token_inactive(
    as_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Introspection and the RS must agree after a live identity change."""

    async def _changed_identity(user_id: str, tenant_id: str) -> AsSession:
        return AsSession(
            user_id=user_id,
            tenant_id=tenant_id,
            role="employee",
            email="user-1@example.test",
            name="Test User",
            auth_version=2,
        )

    monkeypatch.setattr("app.oauth.identity.current_identity", _changed_identity)
    assert (await _post(as_client, token=_access_token())).json() == {"active": False}


# --------------------------------------------------------------------------- #
# 与撤销端点的一致性
# --------------------------------------------------------------------------- #


async def test_revoking_through_the_revocation_endpoint_flips_introspection(
    as_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """走一次真实的撤销请求，然后**用内省去观测它是否真的生效**。

    这条断言把两个端点绑在一起：撤销写下的判据，必须就是内省读的那个。两者的实现
    分处两个模块，只有这条测试能证明它们没有各自漂移。
    """
    from app.oauth.metadata import REVOCATION_PATH

    raw = "refresh-token-value-6"
    async with session_factory() as session:
        session.add(_refresh_token_row(raw))
        await session.commit()
    assert (await _post(as_client, token=raw)).json()["active"] is True

    revoked = await as_client.post(REVOCATION_PATH, data={"token": raw, "client_id": _REGISTERED_CLIENT})
    assert revoked.status_code == 200

    assert (await _post(as_client, token=raw)).json() == {"active": False}
