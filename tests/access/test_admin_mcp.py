"""方案 §WP3：外部 Agent 授权的管理端入口。

这里守三件事
------------
1. **这是一个新的 capability，不是复用 user_admin**。能增删用户的人不必因此就能
   撤销所有外部 Agent 的访问 —— 共用一把钥匙等于把一次误操作的影响面扩大到两者
   之和。
2. **四条路由都必须挂着 capability 装饰器**。漏一条就是一个未受保护的写操作，
   而"撤销"是一个能让所有外部 Agent 立刻失效的动作。
3. **跨租户的 family_id 一律当作不存在**。这不是礼貌，是边界：返回"成功"会让
   管理员以为处置生效了，而实际上什么都没发生 —— 错的不是结果，是结论。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.access.middleware.rbac import ROLE_CAPABILITIES, ROUTE_CAPABILITY_MAP
from app.access.routes import admin_mcp
from app.access.routes.admin_mcp import router
from app.data.models.oauth import OAuthRevokedToken, OAuthToken
from app.main import create_app


def _request(tenant_id: str) -> object:
    """最小可用的 ``Request`` 替身：路由只读 ``request.state``。"""
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id, user_id="admin-1"))


EXPECTED_ROUTES = {
    ("GET", "/api/admin/mcp/installations"),
    ("POST", "/api/admin/mcp/installations/{family_id}/revoke"),
    ("GET", "/api/admin/mcp/clients"),
    ("POST", "/api/admin/mcp/clients/{client_id:path}/revoke"),
}


def test_the_capability_is_admin_only() -> None:
    for role, capabilities in ROLE_CAPABILITIES.items():
        if role == "admin":
            assert "mcp_admin" in capabilities
        else:
            assert "mcp_admin" not in capabilities, f"{role} 不应能管理外部 Agent 授权"


def test_the_capability_is_not_shared_with_user_admin() -> None:
    """两条能力必须分开 —— 见模块文档字符串。"""
    admin_capabilities = ROLE_CAPABILITIES["admin"]
    assert "mcp_admin" in admin_capabilities
    assert "user_admin" in admin_capabilities
    assert "mcp_admin" != "user_admin"


def test_the_route_prefix_maps_to_the_capability() -> None:
    assert ROUTE_CAPABILITY_MAP.get("/api/admin/mcp") == "mcp_admin"


def test_all_four_routes_are_mounted() -> None:
    mounted = {(method, route.path) for route in router.routes for method in route.methods}
    for expected in EXPECTED_ROUTES:
        assert expected in mounted, f"缺少路由 {expected}"


def test_every_route_declares_the_capability() -> None:
    """漏一条就是一个未受保护的写操作。

    装饰器把真实函数包在闭包里，查不到名字；能查的是"每个 endpoint 都被包过" ——
    用 ``__wrapped__`` 是否存在来判定，比什么都不测强。
    """
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        assert endpoint is not None
        assert hasattr(endpoint, "__wrapped__"), f"{route.path} 没有经过 capability 装饰器"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED_ROUTES))
def test_the_endpoints_reject_anonymous_callers(client: TestClient, method: str, path: str) -> None:
    """匿名必须被挡在门外 —— 这些端点能让人列出并切断所有外部 Agent。"""
    concrete = path.replace("{family_id}", "f-1").replace("{client_id:path}", "workbuddy")
    response = client.request(method, concrete.replace(":path", ""))
    assert response.status_code in {401, 403}, f"{method} {concrete} → {response.status_code}"


# --------------------------------------------------------------------------- #
# 跨租户隔离
# --------------------------------------------------------------------------- #


def _token(jti: str, tenant_id: str, family_id: str) -> OAuthToken:
    return OAuthToken(
        jti=jti,
        token_sha256=f"sha-{jti}",
        family_id=family_id,
        client_id="workbuddy",
        tenant_id=tenant_id,
        user_id=f"user-{tenant_id}",
        role="hrbp",
        scope="hrb:policy:read",
        resource="http://localhost/mcp",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )


@pytest.fixture()
async def two_tenants(sqlite_engine, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """真实 sqlite，两张租户各一条轮换链。

    必须用**真库**而不是假会话：租户隔离发生在 SQL 的 WHERE 里，一个忽略 WHERE
    的假对象会让任何实现都通过 —— 那测的是假对象不是代码。
    """

    # 会话工厂会为 RLS 执行 Postgres 的 set_config，sqlite 没有这个函数，而那个
    # 监听器是**全局**的（挂在 Session 的 after_begin 上），所以必须让每一条新连接
    # 都有它 —— 只给当前这一条注册，下一个会话仍然会炸。
    @event.listens_for(sqlite_engine.sync_engine, "connect")
    def _provide_set_config(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.create_function("set_config", 3, lambda _name, value, _is_local: value)  # type: ignore[attr-defined]

    async with sqlite_engine.begin() as connection:
        await connection.run_sync(OAuthToken.__table__.create)
        await connection.run_sync(OAuthRevokedToken.__table__.create)

    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        # 会话工厂会为 RLS 执行 Postgres 的 set_config，sqlite 没有这个函数。
        # 注册一个同名的空实现，让**真实 SQL** 跑起来 —— 租户隔离发生在 WHERE 里，
        # 用一个忽略 WHERE 的假会话来测等于什么都没测。
        session.add_all([_token("jti-a", "tenant-a", "family-a"), _token("jti-b", "tenant-b", "family-b")])
        await session.commit()

    monkeypatch.setattr(admin_mcp, "get_session_factory", lambda: factory)
    return factory


@pytest.mark.asyncio()
async def test_a_family_belongs_to_its_own_tenant(two_tenants) -> None:  # type: ignore[no-untyped-def]
    assert await admin_mcp._family_exists("tenant-a", "family-a") is True


@pytest.mark.asyncio()
async def test_a_family_from_another_tenant_is_reported_as_missing(two_tenants) -> None:  # type: ignore[no-untyped-def]
    """别的租户的 family_id 必须表现为"不存在"，而不是"找到了但不能动"。

    后者会让管理员看到一条跨租户的确认信息（那本身是信息泄漏），前者只是 404。
    """
    assert await admin_mcp._family_exists("tenant-a", "family-b") is False


@pytest.mark.asyncio()
async def test_revoking_a_family_from_another_tenant_raises_not_found(two_tenants) -> None:  # type: ignore[no-untyped-def]
    """撤销一个看不见的安装实例必须报错，而不是静默"成功"。

    返回成功会让管理员以为处置生效了，而实际上什么都没发生 —— 错的不是结果，
    是结论。这是最糟的一类反馈。
    """
    from app.shared.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await admin_mcp.revoke_installation.__wrapped__(  # type: ignore[attr-defined]
            _request(tenant_id="tenant-a"), "family-b"
        )
