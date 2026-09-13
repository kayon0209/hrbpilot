"""动态客户端注册（RFC 7591）—— ADR-0002 §3 定位的**兼容回退**。

默认关闭，且不是"为了保险也开着"
--------------------------------
ADR-0002 §3 的证据：一项对 119 个启用 OAuth 的 MCP 服务器的研究里，119/119 至少存在
一项授权缺陷，96.6% 存在 DCR 相关缺陷。所以 DCR 是"DCR 客户端实测证明必需"时才开的
兼容路径，而不是与 CIMD 并行的主路径。同时开着两者，等于让攻击者可以挑校验更松的
那条走 —— 而 DCR 天生就无法验证"这个客户端是谁"。

开启后仍然受限
--------------
- 限速（滑动窗口，按实例计数）；
- 元数据走**同一套**校验（``validate_client_metadata``），因此 redirect scheme 仍然
  只允许 https 与 loopback、``token_endpoint_auth_method`` 仍然只能是 ``none``；
- 注册结果带 ``dcr_`` 前缀的 client_id，审计上一眼可分。

限速是按**实例**计数的，多实例部署时实际额度是"每实例上限 × 实例数"。这一点写在
这里而不是假装它精确：DCR 默认关闭，而把它做成跨实例精确限速需要引入 Redis 共享状态，
在"默认关着"的前提下一开始就付这个复杂度不划算。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.oauth.clients import ClientMetadata, ClientMetadataError
from app.oauth.metadata import REGISTRATION_PATH
from app.oauth.registry import new_dynamic_client_id, register_dynamic_client
from app.oauth.routes.token import oauth_error
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["oauth-register"])

_WINDOW_SECONDS = 3600.0
_TIMESTAMPS: deque[float] = deque()
_LOCK = threading.Lock()

#: 请求体上限。注册元数据是一小段 JSON。
_MAX_BODY_BYTES = 32 * 1024


def _rate_limit_exceeded() -> bool:
    now = time.monotonic()
    with _LOCK:
        while _TIMESTAMPS and now - _TIMESTAMPS[0] > _WINDOW_SECONDS:
            _TIMESTAMPS.popleft()
        if len(_TIMESTAMPS) >= settings.oauth_dynamic_registration_per_hour:
            return True
        _TIMESTAMPS.append(now)
        return False


def reset_registration_rate_limit() -> None:
    """清空限速窗口。测试用。"""
    with _LOCK:
        _TIMESTAMPS.clear()


def client_registration_response(metadata: ClientMetadata) -> dict[str, object]:
    """RFC 7591 §3.2.1 的注册响应体。"""
    body: dict[str, object] = {
        "client_id": metadata.client_id,
        "client_name": metadata.client_name,
        "redirect_uris": list(metadata.redirect_uris),
        "grant_types": list(metadata.grant_types),
        "response_types": list(metadata.response_types),
        "token_endpoint_auth_method": "none",
        # 明确告诉客户端"这里没有 secret"：RFC 7591 的字段要求给出它，
        # 而给出 0 比省略更清楚（省略容易被读成"实现漏了"）。
        "client_secret_expires_at": 0,
    }
    if metadata.scopes:
        body["scope"] = " ".join(sorted(metadata.scopes))
    return body


@router.post(REGISTRATION_PATH, include_in_schema=False)
async def register(request: Request) -> JSONResponse:
    """动态客户端注册端点。"""
    if not settings.oauth_enable_dynamic_registration:
        # 元数据文档里也不声明这个端点，所以只有硬编码了它的客户端会走到这里。
        # 用 403 而不是 404：前者明确说"这个能力被关掉了"，后者会让客户端以为
        # 自己拼错了地址。
        return oauth_error(
            "invalid_request",
            "dynamic client registration is disabled on this authorization server",
            status_code=403,
        )

    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return oauth_error("invalid_client_metadata", "request body is too large", status_code=400)
    try:
        document = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return oauth_error("invalid_client_metadata", "request body must be valid JSON", status_code=400)
    if not isinstance(document, dict):
        return oauth_error("invalid_client_metadata", "request body must be a JSON object", status_code=400)

    if _rate_limit_exceeded():
        return oauth_error(
            "temporarily_unavailable",
            "too many dynamic client registrations; try again later",
            status_code=429,
        )

    try:
        metadata = await register_dynamic_client(document, new_dynamic_client_id())
    except ClientMetadataError as exc:
        return oauth_error(exc.error, exc.description, status_code=400)

    return JSONResponse(client_registration_response(metadata), status_code=201)
