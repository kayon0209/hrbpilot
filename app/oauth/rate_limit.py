"""OAuth 匿名端点的限流。

为什么按 IP
------------
``authorize`` / ``token`` / ``introspect`` 都**早于任何身份**被调用：客户端此刻
还没有令牌、没有 client 凭据（本 AS 只发 public client，没有 secret 可验）。
能拿到的唯一稳定维度就是来源 IP。

按 ``client_id`` 那一路不在这里做：中间件读表单会**消费请求体**，下游路由再
``await request.form()`` 就拿不到东西了。所以它放在 ``routes/token.py`` 里 ——
那里本来就要解析表单，顺手计数，不产生第二次读取。

已知限制（不是漏做）
--------------------
``request.client.host`` 在反向代理后面取到的是**代理的地址**，于是所有用户共用一个
桶。要拿到真实来源必须先有"可信代理"配置（方案 §WP8），在没有那项配置之前
声称"按客户端限流"是不诚实的。所以这里按 IP **且**在日志里记下这一点：
代理后面的部署必须先把 ``--proxy-headers`` 与可信代理列表配好，否则这个桶会宽到
没有意义。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import settings
from app.guardrails.rate_limiter import RateLimiter
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 各端点的每分钟每 IP 上限。取值不是"越大越好"也不是"越小越好"：
#: authorize 是**人在浏览器里**触发的，正常用户一分钟点不了三十次；
#: token 会被客户端自动重试（refresh 失败、网络抖动），要比 authorize 宽松；
#: introspect 是只读排障端点，最宽松。
_IP_LIMITS: dict[str, tuple[str, int]] = {}


def _limits() -> dict[str, tuple[str, int]]:
    """延迟取值：settings 在测试里会被 monkeypatch，模块级常量就 patch 不到了。"""
    return {
        "/oauth/authorize": ("oauth-authorize-ip", settings.oauth_authorize_per_minute_per_ip),
        "/oauth/token": ("oauth-token-ip", settings.oauth_token_per_minute_per_ip),
        "/oauth/introspect": ("oauth-introspect-ip", settings.oauth_introspect_per_minute_per_ip),
    }


_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


class OAuthRateLimitMiddleware(BaseHTTPMiddleware):
    """按端点 + IP 限流。只保护上面列出的那几个端点，其余一律放行。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/") or "/"
        entry = _limits().get(path)
        if entry is None:
            return await call_next(request)

        bucket, limit = entry
        if limit <= 0:
            return await call_next(request)

        ip_address = _client_ip(request)
        try:
            await RateLimiter().check_bucket(bucket, ip_address, limit)
        except RateLimitError:
            logger.warning("oauth_rate_limited", path=path, ip=ip_address, limit=limit)
            return JSONResponse(
                status_code=429,
                content={"error": "temporarily_unavailable", "error_description": "Too many requests"},
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
        return await call_next(request)
