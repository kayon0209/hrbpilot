"""MCP (Model Context Protocol) surface for HRBPilot.

S scope: read-only tools only. No auth yet; every call is treated as
anonymous and validates through the existing tool whitelist.
"""

from app.mcp.server import mcp_server

__all__ = ["mcp_server"]
