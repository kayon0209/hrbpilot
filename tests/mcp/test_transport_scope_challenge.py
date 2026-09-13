"""协议验收第 6 条：scope 不足必须表达成 **HTTP 403**，不能退化成 200 业务信封。

为什么这需要一个独立文件
------------------------
它测的不是"授权判定"（那由 ``test_authorization_matrix.py`` 覆盖），而是同一个判定
**在 HTTP 层被怎么表达**。MCP 工具层无论返回什么都只能是 HTTP 200 —— 状态码在
工具执行前就定了。所以 403 只能由 ASGI 层的守卫发出，而"守卫是否发出 403"与
"判定是否正确"是两个不同的可失败点。

假 AS 的默认形状态
------------------
``as_server`` 的令牌声明 ``hrb:policy:read hrb:case:propose``，但客户端注册上限只有
``hrb:policy:read``。于是：

- ``search_policy``（要 ``hrb:policy:read``）→ 放行；
- ``create_hr_case``（要 ``hrb:case:propose``）→ 令牌自己声明了，但**有效** scope
  被上限收窄后没有 → ``client_ceiling`` → 403。

这个"令牌自称有、实际上限没有"的落差正是本文件要钉住的情形：它是唯一能区分
"看了令牌自述"与"看了真实上限"的场景。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config.settings import settings
from tests.mcp.conftest import FakeAuthorizationServer


def _tools_call(name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def _post(client: TestClient, token: str, payload: object) -> object:
    return client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json=payload)


def test_insufficient_scope_returns_http_403(as_server: FakeAuthorizationServer, mcp_client: TestClient) -> None:
    response = _post(mcp_client, as_server.token(), _tools_call("create_hr_case", {"case_id": "c-1"}))
    assert response.status_code == 403, f"期望 403，实际 {response.status_code}：{response.text[:200]}"


def test_the_challenge_names_the_missing_scope(as_server: FakeAuthorizationServer, mcp_client: TestClient) -> None:
    """``scope`` 参数必须带：没有它，客户端只能盲目重授权一次。"""
    response = _post(mcp_client, as_server.token(), _tools_call("create_hr_case", {"case_id": "c-1"}))
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="hrb:case:propose"' in challenge


def test_the_challenge_points_at_the_metadata_document(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """403 也要带 ``resource_metadata`` —— 客户端据此知道去哪里补授权。"""
    response = _post(mcp_client, as_server.token(), _tools_call("create_hr_case", {"case_id": "c-1"}))
    assert settings.protected_resource_metadata_url in response.headers["www-authenticate"]
    # 体里也放一份：运维用 curl 打这个端点时不该被逼着去翻响应头。
    assert response.json()["resource_metadata"] == settings.protected_resource_metadata_url


def test_a_tool_whose_scope_is_sufficient_is_not_challenged(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """放行路径不能被守卫误伤 —— 误伤的症状是"原本能用的工具开始 403"。"""
    response = _post(mcp_client, as_server.token(), _tools_call("search_policy", {"query": "请假"}))
    assert response.status_code != 403


def test_handshake_and_discovery_are_never_challenged(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """``initialize`` / ``tools/list`` 不对应任何工具，没有 scope 可判。"""
    for payload in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ):
        response = _post(mcp_client, as_server.token(), payload)
        assert response.status_code != 403, f"{payload['method']} 被守卫误伤"


def test_a_batch_is_challenged_if_any_tool_in_it_lacks_scope(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """批处理里只要有一个不够，整批都要拒 —— 否则客户端会看到"部分成功"。"""
    batch = [
        _tools_call("search_policy", {"query": "请假"}),
        _tools_call("create_hr_case", {"case_id": "c-1"}),
    ]
    response = _post(mcp_client, as_server.token(), batch)
    assert response.status_code == 403


def test_a_batch_whose_tools_are_all_permitted_is_not_challenged(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    batch = [_tools_call("search_policy", {"query": "请假"}), _tools_call("get_policy_source", {"source_id": "s-1"})]
    assert _post(mcp_client, as_server.token(), batch).status_code != 403


def test_a_missing_role_capability_is_not_dressed_up_as_a_scope_problem(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """角色能力不足不是 scope 问题。

    告诉客户端 ``insufficient_scope`` 却不给 scope 参数，等于让它拿着同样的令牌
    重试一次 —— 而角色是服务端配置，重授权解决不了。这一类继续走工具层信封。
    """
    as_server.role = "employee"  # 没有 hr_case 能力
    response = _post(mcp_client, as_server.token(), _tools_call("create_hr_case", {"case_id": "c-1"}))
    assert response.status_code != 403


def test_an_unknown_tool_is_not_challenged(as_server: FakeAuthorizationServer, mcp_client: TestClient) -> None:
    """未知工具不是 scope 问题，它连所需 scope 都不存在。"""
    response = _post(mcp_client, as_server.token(), _tools_call("no_such_tool"))
    assert response.status_code != 403


def test_an_unparseable_body_lets_the_tool_layer_decide(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    """读不出工具名就放行 —— 放行不等于绕过，工具层仍会拦。"""
    response = mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {as_server.token()}", "Content-Type": "application/json"},
        content=b"not json at all",
    )
    assert response.status_code != 403


def test_an_oversized_body_lets_the_tool_layer_decide(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: object
) -> None:
    """宁可少一次 403，也不要为了让 403 生效而缓冲一个无限大的请求体。"""
    from app.mcp import transport_guard

    monkeypatch.setattr(transport_guard, "_MAX_BODY_BYTES", 64)  # type: ignore[attr-defined]
    payload = _tools_call("create_hr_case", {"case_id": "c-1", "padding": "x" * 4096})
    response = _post(mcp_client, as_server.token(), payload)
    assert response.status_code != 403
