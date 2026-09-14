"""AS 登录的会话租户必须由调用方显式指定，不继承 ``user.tenant_id``。

这是"登录租户按客户端路由"的代码落地：令牌由 RS 按 ``tenant_id`` 定位数据，一旦 AS
会话租户与 RS 校验的租户不一致，就会表现为"登录成功却一直 401"。租户来自**授权请求的
客户端**（``authorize`` 传 ``client.tenant_id``），缺省值为 ``DEFAULT_TENANT``。本文件
不依赖真实数据库 —— ``get_db_session`` 与 ``UserRepository`` 都在其源模块上被替换，
只验证钉死逻辑本身。
"""

from __future__ import annotations

from typing import Any

import pytest

import app.data.database
import app.data.repositories.user_repo
from app.oauth.identity import DEFAULT_TENANT, authenticate


class _FakeUser:
    def __init__(self, *, tenant_id: str) -> None:
        self.id = "u-1"
        self.tenant_id = tenant_id
        self.role = "hrbp"
        self.email = "a@example.com"
        self.name = "A"
        self.hashed_password = ""


class _FakeRepo:
    def __init__(self, user: object | None, *, calls: list[str | None] | None = None) -> None:
        self._user = user
        self._calls = calls if calls is not None else []

    async def get_by_email(self, email: str, *, tenant_id: str | None = None) -> object | None:
        self._calls.append(tenant_id)
        return self._user


def _user_with_password(tenant_id: str, password: str = "pw") -> _FakeUser:
    import bcrypt

    user = _FakeUser(tenant_id=tenant_id)
    user.hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return user


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """替换掉会连库的 ``app.data.database.get_db_session``，并记下它被调用的租户。"""
    captured: dict[str, str] = {}

    async def _fake(tenant_id: str = DEFAULT_TENANT):
        captured["tenant_id"] = tenant_id
        yield object()

    # ``authenticate`` 在函数体内 ``from app.data.database import get_db_session``，
    # 每次调用都会重新读取该模块属性，因此 Patch 源模块即可生效。
    monkeypatch.setattr(app.data.database, "get_db_session", _fake)
    return captured


async def test_the_session_tenant_is_the_requested_one_regardless_of_the_user_record(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 数据库中该用户的 tenant_id 是另一个值 —— 会话租户必须仍是调用方指定的那个。
    user = _user_with_password("something-else")
    calls: list[str | None] = []
    monkeypatch.setattr(
        app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(user, calls=calls)
    )

    session = await authenticate("a@example.com", "pw", tenant_id="acme")

    assert session is not None
    assert session.tenant_id == "acme"
    # 登录查询确实发生在该租户的 RLS 上下文里，且查询本身带显式租户过滤。
    assert patched["tenant_id"] == "acme"
    assert calls == ["acme"]


async def test_the_default_tenant_is_used_when_none_is_requested(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_password("something-else")
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(user))

    session = await authenticate("a@example.com", "pw")

    assert session is not None
    assert session.tenant_id == DEFAULT_TENANT
    assert patched["tenant_id"] == DEFAULT_TENANT


async def test_unknown_user_returns_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(None))
    assert await authenticate("a@example.com", "pw", tenant_id="acme") is None


async def test_bad_password_returns_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_password("acme", "pw")
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(user))
    assert await authenticate("a@example.com", "wrong-password", tenant_id="acme") is None


async def test_empty_credentials_return_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(_user_with_password(DEFAULT_TENANT))
    )
    assert await authenticate("", "pw") is None
    assert await authenticate("a@example.com", "") is None
    assert await authenticate("a@example.com", "pw", tenant_id="") is None


async def test_the_email_lookup_is_filtered_by_tenant_explicitly(
    sqlite_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别的租户的用户记录，不能被拿来给本租户的客户端登录。

    ``email`` 是全局唯一列，"按邮箱查"不带租户条件时查回来的记录可能属于**任何**
    租户 —— 而令牌的 tenant_id 来自客户端。若不过滤，缺省租户的用户会以 acme 的
    租户身份拿到令牌（user_id 与 tenant_id 属于两个租户）。RLS 是否生效取决于部署
    （角色是否表 owner、是否 FORCE），所以过滤必须发生在**查询里**。e2e 在真实
    进程里抓到过 owner-bypass 版本的跨租户命中，这里是它的单元层守卫。
    """
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.data.models.user import User
    from app.data.repositories.user_repo import UserRepository

    async with sqlite_engine.begin() as connection:
        await connection.run_sync(User.__table__.create)

    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(
                id=str(uuid4()),
                tenant_id="default",
                email="same@example.com",
                name="A",
                role="hrbp",
                hashed_password="x",
            )
        )
        await session.commit()

    async with factory() as session:
        repo = UserRepository(session)
        hit = await repo.get_by_email("same@example.com", tenant_id="default")
        miss = await repo.get_by_email("same@example.com", tenant_id="acme")
        legacy = await repo.get_by_email("same@example.com")
    assert hit is not None and hit.tenant_id == "default"
    assert miss is None
    # 平台登录不带租户参数，行为保持不变。
    assert legacy is not None
