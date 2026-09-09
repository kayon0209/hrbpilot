"""MCP S-scope smoke: server is reachable, tools validate via whitelist."""

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.anyio
async def test_mcp_ping() -> None:
    from app.mcp.server import mcp_server

    tools = await mcp_server.list_tools()
    names = {t.name for t in tools}
    assert {"search_policy", "get_policy_source", "hrbpilot_ping"}.issubset(names)

    result = await mcp_server.call_tool("hrbpilot_ping", {})
    assert result.is_error is False
    assert result.structured_content and result.structured_content.get("ok") is True


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
        r = await client.call_tool("hrbpilot_ping", {})
        assert r.is_error is False


@pytest.mark.anyio
async def test_mcp_capabilities_resource() -> None:
    from app.mcp.server import mcp_server

    resources = await mcp_server.list_resources()
    assert any(str(r.uri) == "hrbpilot://capabilities" for r in resources)
    contents = await mcp_server.read_resource("hrbpilot://capabilities")
    assert contents is not None
