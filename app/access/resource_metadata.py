"""MCP Resource Server 的发现面：RFC 9728 元数据文档与 401 挑战头。

为什么需要它
------------
引入外部 Agent 之前，本服务**没有任何机制告诉客户端"去哪里拿令牌"**。未认证调用
拿到的是 HTTP 200 加一个业务信封 ``AUTH_REQUIRED`` —— 对 MCP 客户端而言这既不是
成功（没有数据）也不是挑战（没有 401），于是它不知道自己该做什么，只能放弃。

规范要求的是这条具体的发现链路：

1. 客户端不带凭据请求 ``/mcp``；
2. 服务端返回 **401**，且 ``WWW-Authenticate`` 头带 ``resource_metadata`` 参数；
3. 客户端取该 URL，得到 RFC 9728 Protected Resource Metadata 文档，从中读到
   ``authorization_servers`` 与 ``scopes_supported``；
4. 客户端凭这些信息走 OAuth 授权码 + PKCE，拿到 audience 绑定到 ``resource``
   的 access token；
5. 带令牌重试 ``/mcp``。

本模块只负责第 2、3 步。

为什么放在 access 层
--------------------
它需要被 HTTP 中间件使用（``app/access/middleware/auth.py`` 构造 401 挑战）。而本层
有一条既有约定：**中间件不依赖 ``app.mcp.*``**（见 ``app/access/tokens.py`` 顶部
关于 ``SCOPE_AUTH_METHOD_INTERNAL`` 的说明 —— 那个常量定义在 access 层正是为了
这一点）。发现面是资源服务器的**对外契约**，与 ``tokens`` / ``scopes`` 同层，
而不是"mcp 包的内部实现"。

为什么是纯函数
--------------
这条链路的正确性**没有观测手段**：客户端不会把"我读到的 issuer 不是 https"或
"挑战头少了一个引号"报回来，它只会静默地走不通。因此能做的只有把构造逻辑做成
无 IO、无上下文的纯函数，然后用逐字节断言把它钉死 —— 包括负例（未配置 AS 时
字段必须缺席，而不是空数组）。``tests/mcp/test_resource_metadata.py`` 是它的守卫。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.access.scopes import Scope
from app.config.settings import settings

#: RFC 6750 §3 的注册错误码。只在凭据**确实被提供但不被接受**时才带 ``error``；
#: 完全没带凭据的请求不带 —— 后者不是错误，只是授权流程还没开始，把它标成
#: invalid_token 会让客户端误以为"我的令牌坏了"而放弃重试。
BEARER_ERROR_INVALID_TOKEN = "invalid_token"

#: RFC 6750 §3.1：凭据有效但授权范围不足。必须配 ``scope`` 参数才有意义 ——
#: 只说"范围不够"而不说"还缺什么"，客户端只能盲目重授权一次，很可能还是不够。
BEARER_ERROR_INSUFFICIENT_SCOPE = "insufficient_scope"


def _quote(value: str) -> str:
    """按 RFC 7235 ``quoted-string`` 转义。

    值来自配置而非请求，但仍然转义：配置里的一个引号会把整个挑战头变成语法错误，
    而客户端解析失败时只会当作"没有挑战"处理 —— 又是一个静默失败。
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def protected_resource_metadata() -> dict[str, Any]:
    """RFC 9728 §2 的 Protected Resource Metadata 文档。

    ``authorization_servers`` 在**未配置时被省略**，而不是返回空数组。理由：空数组
    的语义在 RFC 9728 里没有定义，客户端无法区分"明确没有 AS"与"字段没写好"；
    省略则触发了规范定义的 fallback（客户端改用 ``resource`` 的 origin），
    且缺失本身就是一个可见信号 —— 比一个含义不明的空数组诚实。
    """
    document: dict[str, Any] = {
        # RFC 8707：access token 的 audience 必须逐字节等于这个值。
        "resource": settings.mcp_resource_url,
        "resource_name": "HRBPilot MCP",
        # 只支持 Authorization 头。本服务不接受 query 参数传令牌 —— 它会进
        # 访问日志、Referer 与浏览器历史，而这里承载的是员工敏感数据。
        "bearer_methods_supported": ["header"],
        # 全部 scope 词表（含尚无工具消费的两个）：这份文档描述**服务支持的**
        # 授权范围，不是某个客户端的授权结果。客户端据此申请，而不是逐个试探。
        "scopes_supported": sorted(scope.value for scope in Scope),
    }
    servers = settings.authorization_servers
    if servers:
        document["authorization_servers"] = list(servers)
    return document


def www_authenticate_challenge(*, error: str | None = None, scope: Iterable[Scope] | None = None) -> str:
    """构造 ``WWW-Authenticate`` 头的值（不含头名）。

    参数顺序固定，让测试可以逐字节断言 —— 客户端的解析是按 auth-param 列表读的，
    顺序无关，但"顺序无关"是规范给的自由度，不是我们放弃可断言性的理由。

    ``scope`` 默认**不传**：传输层看到的是一次 HTTP 请求，它可能是 ``initialize``
    或 ``tools/list``，并不对应任何具体工具，因此不知道这次需要哪些 scope。猜一个
    比不给更糟 —— 客户端会拿着范围不对的令牌反复重试，而提示本身是错的。
    需要 scope 提示的是工具层的拒绝（那里知道是哪个工具），不属于本函数。
    """
    params: list[str] = []
    if error:
        params.append(f'error="{_quote(error)}"')
    if scope:
        params.append(f'scope="{_quote(" ".join(item.value for item in scope))}"')
    params.append(f'resource_metadata="{_quote(settings.protected_resource_metadata_url)}"')
    return "Bearer " + ", ".join(params)
