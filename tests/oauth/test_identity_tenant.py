"""AS 登录必须把会话租户钉死为 ``DEFAULT_TENANT``，不继承 ``user.tenant_id``。

这是"登录租户固定为 default"的代码落地：令牌由 RS 按 ``tenant_id`` 定位数据，一旦 AS
会话租户与 RS 校验的租户不一致，就会表现为"登录成功却一直 401"。本文件不依赖真实数据库
—— ``get_db_session`` 与 ``UserRepository`` 都在其源模块上被替换，只验证钉死逻辑本身。
"""

from __future__ import annotations

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
    def __init__(self, user: object | None) -> None:
        self._user = user

    async def get_by_email(self, email: str) -> object | None:
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


async def test_session_tenant_is_pinned_to_default_regardless_of_user_record(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 数据库中该用户的 tenant_id 是另一个值 —— 会话租户仍必须是 DEFAULT_TENANT。
    user = _user_with_password("enterprise-7")
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(user))

    session = await authenticate("a@example.com", "pw")

    assert session is not None
    assert session.tenant_id == DEFAULT_TENANT
    # 登录查询确实发生在默认租户的 RLS 上下文里。
    assert patched["tenant_id"] == DEFAULT_TENANT


async def test_unknown_user_returns_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(None))
    assert await authenticate("a@example.com", "pw") is None


async def test_bad_password_returns_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_password(DEFAULT_TENANT, "pw")
    monkeypatch.setattr(app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(user))
    assert await authenticate("a@example.com", "wrong-password") is None


async def test_empty_credentials_return_none(
    patched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app.data.repositories.user_repo, "UserRepository", lambda db: _FakeRepo(_user_with_password(DEFAULT_TENANT))
    )
    assert await authenticate("", "pw") is None
    assert await authenticate("a@example.com", "") is None
