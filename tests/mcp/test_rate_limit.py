"""方案 §WP3：MCP 调用的限流维度与 429 表达。

这里测的是**维度**而不是算法
----------------------------
滑动窗口计数由 ``app.guardrails.rate_limiter`` 自己负责（Redis 语义、窗口清理、
多实例一致性）。本文件关心的是另一件事：一次 ``/mcp`` 调用到底**按哪些维度**计数。
维度选错的后果不是"限流不准"，而是**看不出是谁干的** —— 一个失控的 Agent 会占掉
为它授权的那个人的配额，而按用户/租户排查时完全定位不到客户端。

所以这里用一个只做记录的假限流器：它不判断阈值，只记录"谁被计了数"，由测试断言
维度集合是否正确。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.mcp import transport_guard
from app.shared.errors import RateLimitError
from tests.mcp.conftest import FakeAuthorizationServer


class _RecordingLimiter:
    """记录每次计数的 ``(bucket, key)``，从不真的限流。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, str]] = []

    async def check_bucket(self, bucket: str, key: str, limit: int) -> None:
        self.calls.append((bucket, key))


class _AlwaysOverLimiter:
    """任何维度都判超阈值 —— 用来观测 429 的形状。"""

    async def check_bucket(self, bucket: str, key: str, limit: int) -> None:
        raise RateLimitError("请求过于频繁，请稍后再试")


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> _RecordingLimiter:
    limiter = _RecordingLimiter()
    monkeypatch.setattr(transport_guard, "RateLimiter", lambda *a, **k: limiter)
    return limiter


def _tools_call(name: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}


def test_a_call_is_counted_on_user_client_and_installation(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, recorded: _RecordingLimiter
) -> None:
    """三个维度都要计数 —— 少一个就少一种排查视角。"""
    mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy"),
    )
    buckets = {bucket for bucket, _ in recorded.calls}
    assert buckets == {"mcp-user", "mcp-client", "mcp-installation"}


def test_the_client_bucket_is_keyed_by_the_client_not_the_user(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, recorded: _RecordingLimiter
) -> None:
    """客户端桶按 client_id 计：同一个人的两个 Agent 不能共享同一个桶。"""
    mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy"),
    )
    by_bucket = dict(recorded.calls)
    assert by_bucket["mcp-client"] == as_server.client_id
    assert by_bucket["mcp-installation"] == as_server.family_id


def test_throttling_returns_429_with_retry_after(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transport_guard, "RateLimiter", lambda *a, **k: _AlwaysOverLimiter())
    response = mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy"),
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after"), "429 必须带 Retry-After，否则客户端只会立刻重试"


def test_a_normal_call_is_not_throttled(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, recorded: _RecordingLimiter
) -> None:
    response = mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy"),
    )
    assert response.status_code != 429


def test_throttling_happens_before_the_guard_resolves_the_principal(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """被限流的请求不该再消耗守卫自己的那次解析。

    一次 ``/mcp`` 调用正常会解析两次令牌：认证中间件一次（它必须解，否则不知道
    该不该 401），守卫一次（它需要完整的主体才能判 scope）。被限流时第二次不该
    发生 —— 所以这里断言的是**次数**而不是"有没有查过库"。
    """
    monkeypatch.setattr(transport_guard, "RateLimiter", lambda *a, **k: _AlwaysOverLimiter())
    # 直接打 /mcp/：/mcp 会 307 到 /mcp/，而重定向会让认证中间件跑两次，
    # 计数就多出一倍 —— 那测的就不是"解析了几次"，而是"重定向了几次"。
    mcp_client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("create_hr_case"),
    )
    assert len(as_server.lookup_log) == 1, (
        f"被限流的请求仍然做了守卫的解析（共 {len(as_server.lookup_log)} 次）—— 限流位置太靠后"
    )


def test_an_allowed_call_resolves_the_principal_twice_before_the_tool_layer(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, recorded: _RecordingLimiter
) -> None:
    """到达工具层之前有两次令牌解析，把这个数字钉住。

    1. 认证中间件（``_dispatch_mcp``）：它必须解，否则不知道该 401 还是放行；
    2. 传输层守卫：它需要完整主体（含 scope 与客户端上限）才能判 403。

    第二次是守卫引入的。收敛成一次需要让守卫复用中间件已解析的 claims
    （中间件目前只把 user/role/tenant 写进 scope）。这不是不能做，但会让
    ``request.scope["auth"]`` 承载越来越多东西，而那份字典是三层中间件共享的
    —— 为了省一次索引查询去扩大它的契约，不划算。

    **这条例外说明**：工具层（``_principal_from_ctx``）还会再解一次，但本夹具
    不进 lifespan，MCP 子应用不会真正执行工具，所以这里数不到它。那一层由
    ``test_authorization_matrix.py`` 与端到端脚本覆盖。

    数字变成 3（或更多）时必须有人解释为什么；变少则要先确认不是少了必要的一层。
    """
    mcp_client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy"),
    )
    assert len(as_server.lookup_log) == 2, (
        f"到达工具层前的解析次数变为 {len(as_server.lookup_log)}，与上面记录的两次不符"
    )


def test_an_unauthenticated_request_is_not_counted(mcp_client: TestClient, recorded: _RecordingLimiter) -> None:
    """没有身份就没有维度可计 —— 它在更外层就被 401 挡掉了。"""
    mcp_client.post("/mcp", json=_tools_call("search_policy"))
    assert recorded.calls == []
