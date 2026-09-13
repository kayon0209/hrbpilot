"""MCP (Model Context Protocol) surface for HRBPilot.

Exposes the HR Case tool catalog over MCP: 2 read tools (tenant-scoped policy
retrieval, shared with the agent loop) and 5 write tools that only ever create an
``ApprovalRequest`` — a human with ``hr_manager`` decides before anything runs.

模块内容：
- ``server``: MCP 协议出口（stdio + Streamable HTTP），工具注册与认证解析；
- ``read_dispatch``: 读工具的统一派发（协议出口与 REST 桥共用）；
- ``contract``: 统一返回契约（outcome + 中文用户文案）。
"""

from app.mcp.server import mcp_server

__all__ = ["mcp_server"]
