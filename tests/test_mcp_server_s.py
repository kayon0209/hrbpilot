"""MCP S-scope smoke: server is reachable, tools validate via whitelist."""

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.anyio
async def test_mcp_lists_the_business_tools() -> None:
    """服务端暴露的工具集合。

    这里刻意列的是**业务工具**：早期版本还有一个 ``hrbpilot_ping`` 健康探针，它注册在
    MCP 出口上却不在工具目录里 —— 也就是说它完全绕过了授权判定。它已移除：
    对外的 Agent 出口上不该有开发者的调试探针，而"我的令牌好不好用"这个问题由
    ``get_my_access_profile`` 回答，它是走授权判定的。
    """
    from app.mcp.server import mcp_server

    tools = await mcp_server.list_tools()
    names = {t.name for t in tools}
    assert {"search_policy", "get_policy_source", "get_my_access_profile"}.issubset(names)
    assert "hrbpilot_ping" not in names, "开发者探针不该出现在对外工具清单里"


@pytest.mark.anyio
async def test_mcp_read_tools_validate() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from app.mcp.server import mcp_server

    ok = await mcp_server.call_tool("search_policy", {"query": "加班费", "top_k": 2})
    assert ok.is_error is False
    assert ok.structured_content and ok.structured_content["tool"] == "search_policy"

    with pytest.raises(ToolError):
        await mcp_server.call_tool("search_policy", {})


@pytest.mark.anyio
async def test_mcp_in_memory_client_roundtrip() -> None:
    from mcp import Client
    from mcp.client._memory import InMemoryTransport

    from app.mcp.server import mcp_server

    transport = InMemoryTransport(mcp_server)  # type: ignore[arg-type]
    async with Client(transport) as client:  # type: ignore[arg-type]
        tools = await client.list_tools()
        assert any(t.name == "search_policy" for t in tools.tools)
        r = await client.call_tool("get_my_access_profile", {})
        assert r.is_error is False


@pytest.mark.anyio
async def test_mcp_capabilities_resource() -> None:
    from app.mcp.server import mcp_server

    resources = await mcp_server.list_resources()
    assert any(str(r.uri) == "hrbpilot://capabilities" for r in resources)
    contents = await mcp_server.read_resource("hrbpilot://capabilities")
    assert contents is not None
