"""MCP 身份与授权 —— 主体、凭据适配、唯一授权决策。

模块内容：

- ``principal``: ``McpPrincipal`` / ``AuthMethod`` —— 一次调用的已验证身份；
- ``adapters``: 凭据（Authorization 头 / 已验证 claim）→ 主体；
- ``authorization``: ``authorize_tool_call`` 与 ``denial_envelope`` —— 唯一的判定
  与拒绝响应构造，两条出口共用。

为什么合并成一个包
------------------
这三件事过去散落在 ``app/mcp/server.py`` 的 ``_auth_from_ctx()``（返回裸 dict）与
``app/access/routes/mcp.py`` 的 ``_caller_role()`` 里，于是"身份"有了两种表示、
"判定"有了两条路径。收敛到一个包之后，``McpPrincipal`` 是唯一表示，
``authorize_tool_call`` 是唯一判定。

导入约定：本包**只**依赖 ``app.access.*`` 与 ``app.mcp.contract``（叶子模块），
不导入 ``app.mcp.server`` —— 后者反过来依赖本包，反向导入会成环。
"""

from app.mcp.auth.adapters import (
    principal_from_authorization_header,
    principal_from_bearer_token,
    principal_from_claims,
    principal_from_headers,
    verified_principal,
)
from app.mcp.auth.authorization import (
    DenyReason,
    ToolAuthorization,
    authorize_tool_call,
    denial_envelope,
    log_denial,
    visible_tools,
)
from app.mcp.auth.principal import INTERNAL_CLIENT_ID, AuthMethod, McpPrincipal

__all__ = [
    "INTERNAL_CLIENT_ID",
    "AuthMethod",
    "DenyReason",
    "McpPrincipal",
    "ToolAuthorization",
    "authorize_tool_call",
    "denial_envelope",
    "log_denial",
    "principal_from_authorization_header",
    "principal_from_bearer_token",
    "principal_from_claims",
    "principal_from_headers",
    "verified_principal",
    "visible_tools",
]
