"""/mcp 传输层的 **403 挑战**：把 scope 类拒绝翻译成标准 HTTP 响应。

为什么需要它
------------
方案 §4.2 与协议验收第 6 条要求：scope 不足返回 **403 + 可机器理解的错误和需要的
scope**，"不能退化成 HTTP 200 业务错误"。而 MCP 工具层做不到这件事 —— 它运行在
JSON-RPC 里，无论返回什么都是一次 HTTP 200，因为 HTTP 状态码在工具执行**之前**
就已经定了（客户端拿到 200 再解析 body 里的错误）。

所以 403 只能由 HTTP 层发出。本模块就是那层：它包在挂载的 MCP 子应用外面，
读一次 JSON-RPC 请求体取出工具名，然后决定放行还是 403。

为什么它不是"第二个授权判定点"
------------------------------
直觉上"在传输层再判一次授权"正是 WP1 用整条工作包消掉的那类缺陷 —— 两处判定
会漂移，而漂移方向是安全故障。这里刻意避免了这一点：

- 判定的**函数**是同一个：``app.mcp.auth.authorize_tool_call``；
- 判定的**输入**是同一组：同一个主体、同一个工具名、同一个 ``TOOL_CATALOG``；
- 该函数是**纯函数**（无 IO），所以两次调用对同一输入必然同一输出 ——
  不存在"两处结论不同"的可能，只存在"同一结论被表达两次"。

工具层在 ``app/mcp/server.py`` 里仍然调用它。那一次才是真正的执行期拦截
（发现侧隐藏了工具、或有人手工直调时，靠它兜住）；本模块只是把**已经注定**的
拒绝提前到 HTTP 层，好让它能被表达成 403。

换句话说：本模块**不产生**结论，只**搬运**结论。

为什么只处理 scope 类拒绝
--------------------------
角色能力不足（``missing_capability``）也是"已认证但不放行"，但它没有对应的
scope 可以提示 —— 告诉客户端 ``insufficient_scope`` 却不给 scope 参数，等于让它
拿着同样的令牌去重试一次。角色是服务端配置，客户端无法通过补授权解决。
这一类因此继续走工具层信封，由 ``denial_envelope`` 给中文文案。

为什么允许"读一次请求体"
------------------------
ASGI 里请求体只能被消费一次，读了就得原样还给下游。这里做了长度上限与"读不完
就整体放行"的处理 —— 放行不代表绕过：工具层仍会拦截，只是那次拒绝表现为
JSON-RPC 错误而不是 403。宁可少一次 403，也不要为了让 403 生效而缓冲一个
无限大的请求体。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.responses import JSONResponse

from app.access.resource_metadata import (
    BEARER_ERROR_INSUFFICIENT_SCOPE,
    www_authenticate_challenge,
)
from app.access.scopes import Scope
from app.config.settings import settings
from app.guardrails.rate_limiter import RateLimiter
from app.mcp.audit import record_mcp_call
from app.mcp.auth import McpPrincipal, authorize_tool_call, log_denial, principal_from_headers
from app.mcp.auth.authorization import DenyReason
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 限流窗口长度（秒），与 ``app.guardrails.rate_limiter`` 的窗口一致。
#: 用于 ``Retry-After`` —— 告诉客户端"多久之后重试有意义"，而不是让它自己猜。
_WINDOW_SECONDS = 60

#: ASGI 三件套。显式写出来而不是一律用 ``Any``：``receive`` 的返回类型决定了
#: 取到的消息要不要再判空，``app`` 的类型决定了它能不能被 ``mount`` 接受 ——
#: 这两处正是 mypy 唯一能替我们兜住的地方。
AsgiScope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[AsgiScope, Receive, Send], Awaitable[None]]

#: 请求体缓冲上限。超过就整体放行 —— 见模块文档字符串最后一段。
_MAX_BODY_BYTES = 1 << 20

#: 会被翻译成 403 的拒绝原因。只有这两个与"scope 不够"有关，见模块文档字符串。
_SCOPE_DENIALS = frozenset({DenyReason.MISSING_SCOPE, DenyReason.CLIENT_CEILING})

_SURFACE = "mcp_transport_guard"


def _headers_from_scope(scope: AsgiScope) -> dict[str, str]:
    """ASGI 原始头 → 小写名映射。

    ASGI 规范用 latin-1 编码头值。``principal_from_headers`` 自己会再归一一次，
    这里多归一一次不是冗余：它保证传给下游的是"HTTP 语义上的头"，而不是
    "ASGI 的字节元组"。
    """
    raw = scope.get("headers") or []
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in raw}


def _as_object(value: Any) -> object:
    """把 json.loads 的 Any 收成 object —— 否则 mypy 会一路把 Any 往外传。"""
    return value


def _parse_json_rpc(body: bytes) -> object | None:
    """解析 JSON-RPC 请求体。解析不了返回 ``None``（= 没有信息可判）。"""
    try:
        return _as_object(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _messages(payload: object) -> list[Any]:
    """把单个请求与批处理数组统一成消息列表。"""
    if isinstance(payload, list):
        return list(payload)
    return [payload] if payload is not None else []


def _tool_names(payload: object) -> tuple[str, ...]:
    """从 JSON-RPC 请求体里取出被调用的工具名。

    覆盖三种形态：单个 ``tools/call``、JSON-RPC 批处理数组、以及与之无关的
    其它方法（``initialize`` / ``tools/list`` / 通知）。后者返回空元组 ——
    没有工具名就没有 scope 可判，直接放行。
    """
    names: list[str] = []
    for message in _messages(payload):
        if not isinstance(message, dict):
            continue
        if message.get("method") != "tools/call":
            continue
        params = message.get("params")
        if not isinstance(params, dict):
            continue
        name = params.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return tuple(names)


def _arguments_of(payload: object, tool_name: str) -> dict[str, Any] | None:
    """取某个 ``tools/call`` 的参数，供审计算摘要（只算摘要，不落原文）。"""
    for message in _messages(payload):
        if not isinstance(message, dict) or message.get("method") != "tools/call":
            continue
        params = message.get("params")
        if not isinstance(params, dict) or params.get("name") != tool_name:
            continue
        arguments = params.get("arguments")
        if isinstance(arguments, dict):
            return arguments
    return None


def _insufficient_scope_response(required_scope: str) -> JSONResponse:
    """403 + ``WWW-Authenticate: ... insufficient_scope, scope="..."``。

    ``scope`` 参数必须带：只说"范围不够"而不说"还缺什么"，客户端只能盲目重授权
    一次，很可能仍然不够 —— 那个错误提示本身就是错的。
    """
    try:
        scope = Scope(required_scope)
    except ValueError:
        scope = None
    challenge = www_authenticate_challenge(
        error=BEARER_ERROR_INSUFFICIENT_SCOPE,
        scope=[scope] if scope else None,
    )
    return JSONResponse(
        status_code=403,
        content={
            "code": "FORBIDDEN",
            "status": 403,
            "message": "Insufficient scope for this tool",
            "resource_metadata": settings.protected_resource_metadata_url,
        },
        headers={"WWW-Authenticate": challenge},
    )


async def _resolve_principal(scope: AsgiScope) -> McpPrincipal | None:
    return await principal_from_headers(_headers_from_scope(scope))


async def _rate_limit_response(scope: AsgiScope) -> JSONResponse | None:
    """/mcp 的限流：按 用户 / 客户端 / 安装实例 三个维度分别计数。

    维度取自 ``scope["auth"]``（由 ``AuthMiddleware._dispatch_mcp`` 写入），
    因此这里不需要再解析一次令牌 —— 那会多一次 JWKS 取用与数据库查询。

    为什么要按客户端与安装实例单独计数：一个行为异常的 Agent 会占掉为它授权的
    **那个用户**的配额，而按用户看不出是哪个客户端干的，按租户更看不出来
    （方案 §WP3：避免登录流量挤占业务调用）。

    平台自签令牌没有 client / installation 维度，这时只按用户计数。**不是**静默
    跳过：少两个维度会让这个令牌的实际额度更宽松，这一点必须能被看出来 ——
    所以下面的桶是显式按"维度存在才计"组织的。
    """
    auth = scope.get("auth")
    if not isinstance(auth, dict):
        return None

    limiter = RateLimiter()
    user_id = auth.get("user_id")
    client_id = auth.get("client_id")
    installation_id = auth.get("installation_id")

    checks: list[tuple[str, str, int]] = []
    if user_id:
        checks.append(("mcp-user", str(user_id), settings.mcp_rate_limit_user_per_minute))
    if client_id:
        checks.append(("mcp-client", str(client_id), settings.mcp_rate_limit_client_per_minute))
    if installation_id:
        checks.append(("mcp-installation", str(installation_id), settings.mcp_rate_limit_installation_per_minute))

    try:
        await limiter.check_buckets(checks)
    except RateLimitError as exc:
        logger.warning(
            "mcp_rate_limited",
            user_id=user_id,
            client_id=client_id,
            installation_id=installation_id,
        )
        # 429 必须带 Retry-After：否则客户端唯一能做的合理反应就是立刻重试，
        # 于是限流本身变成了放大流量的东西。
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "status": exc.status_code, "message": exc.message},
            headers={"Retry-After": str(_WINDOW_SECONDS)},
        )
    return None


class InsufficientScopeGuard:
    """把 scope 类拒绝翻译成 HTTP 403 的 ASGI 中间件。

    只在 ``/mcp`` 上启用（``app.main`` 里包住挂载的子应用）。它不处理认证 ——
    那属于 ``AuthMiddleware._dispatch_mcp``，本模块运行在它之后，因此到这里时
    凭据一定已经验过签；这里拿不到主体（例如 stdio 不会有 HTTP 层）就放行，
    交给工具层按匿名处理。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        throttled = await _rate_limit_response(scope)
        if throttled is not None:
            await throttled(scope, receive, send)
            return

        chunks, complete = await self._buffer(receive)
        if not complete:
            # 读不完（超上限或连接异常）就整体放行：工具层仍然会拦截。
            await self.app(scope, self._replay(chunks, receive), send)
            return

        body = b"".join(chunk.get("body", b"") for chunk in chunks)
        payload = _parse_json_rpc(body)
        names = _tool_names(payload)
        if not names:
            await self.app(scope, self._replay(chunks, receive), send)
            return

        principal = await _resolve_principal(scope)
        for name in names:
            decision = authorize_tool_call(principal, name, catalog=TOOL_CATALOG)
            if decision.allowed or decision.deny_reason not in _SCOPE_DENIALS:
                continue
            log_denial(decision, principal, surface=_SURFACE)
            # 这一层拒绝之后 MCP 子应用不会被调用，工具层的审计点也就不会执行 ——
            # 所以 403 必须自己留痕，否则这类拒绝在审计里完全消失。
            await record_mcp_call(
                principal,
                tool=name,
                outcome_code="FORBIDDEN",
                deny_reason=decision.deny_reason.value if decision.deny_reason else None,
                params=_arguments_of(payload, name),
            )
            if decision.required_scope is None:
                continue
            response = _insufficient_scope_response(decision.required_scope.value)
            await response(scope, receive, send)
            return

        await self.app(scope, self._replay(chunks, receive), send)

    @staticmethod
    async def _buffer(receive: Receive) -> tuple[list[Message], bool]:
        """消费并暂存整个请求体。返回 ``(消息列表, 是否完整读完)``。"""
        chunks: list[Message] = []
        total = 0
        while True:
            message = await receive()
            chunks.append(message)
            total += len(message.get("body", b"") or b"")
            # 尺寸检查必须**先于** more_body 判断：整个请求体一次性送达时（小载荷
            # 的常见形态）more_body 为 False，若先判断它就会带着"读完了"直接返回，
            # 上限形同虚设。
            if total > _MAX_BODY_BYTES:
                return chunks, False
            if not message.get("more_body", False):
                return chunks, True

    @staticmethod
    def _replay(chunks: list[Message], receive: Receive) -> Receive:
        """把暂存的消息原样还给下游，之后交回原始 ``receive``。"""

        async def _receive() -> Message:
            if chunks:
                return chunks.pop(0)
            return await receive()

        return _receive
