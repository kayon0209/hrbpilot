"""MCP server for HRBPilot — S scope: read-only HR + policy read tools."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.server import MCPServer

from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call

mcp_server = MCPServer("hrbpilot-mcp")

_READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")


@mcp_server.tool(name="search_policy", description="Read-only: validates search_policy params against the HR Case Agent whitelist and returns the normalized payload. S scope — no live retrieval yet.")
async def search_policy(query: str, kb_id: str | None = None, top_k: int = 3) -> dict[str, Any]:
    params: dict[str, Any] = {"query": query, "top_k": top_k}
    if kb_id is not None:
        params["kb_id"] = kb_id
    validated = validate_tool_call("search_policy", params)
    return {"tool": "search_policy", "validated_params": validated, "note": "S scope: validated only; live RAG wiring is P1."}


@mcp_server.tool(name="get_policy_source", description="Read-only: validates get_policy_source params against the HR Case Agent whitelist and returns the normalized payload. S scope.")
async def get_policy_source(document_name: str, section: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"document_name": document_name}
    if section is not None:
        params["section"] = section
    validated = validate_tool_call("get_policy_source", params)
    return {"tool": "get_policy_source", "validated_params": validated, "note": "S scope: validated only."}


@mcp_server.tool(name="hrbpilot_ping", description="Health/debug tool for the HRBPilot MCP server.")
async def hrbpilot_ping() -> dict[str, Any]:
    return {"ok": True, "server": "hrbpilot-mcp", "scope": "S-read-only", "read_tools": sorted(_READ_TOOL_NAMES)}


@mcp_server.resource("hrbpilot://capabilities")
async def hrbpilot_capabilities() -> str:
    payload = {
        "server": "hrbpilot-mcp",
        "scope": "S",
        "read_tools": sorted(_READ_TOOL_NAMES),
        "write_tools_exposed": False,
        "note": "M scope would map write tools to 'create approval request'.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp_server.run()
