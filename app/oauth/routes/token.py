"""令牌端点与撤销端点（RFC 6749 §5、RFC 7009）。

响应头里的 ``Cache-Control: no-store``
--------------------------------------
令牌响应是**唯一**不能出现在任何缓存里的东西：它是 bearer 凭据本身。RFC 6749 §5.1
把它列为 MUST。撤销响应同样带上 —— 它虽然不含令牌，但会泄漏"这个值曾经是有效的"。
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.guardrails.rate_limiter import RateLimiter
from app.oauth.authorization import AuthorizationCodeError, redeem_authorization_code
from app.oauth.metadata import REVOCATION_PATH, TOKEN_PATH
from app.oauth.registry import resolve_client
from app.oauth.tokens import TokenError, issue_tokens_for_authorization, revoke_token, rotate_refresh_token
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["oauth-token"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_WINDOW_SECONDS = 60


async def _throttle_client(client_id: str) -> JSONResponse | None:
    """按 client_id 限流。返回 ``None`` 表示未超限。

    为什么要单独一维：IP 维度在 NAT 或同一台机器上跑多个客户端时是**共用的** ——
    一个失控的客户端会把同 IP 上所有人的额度耗光，而按 IP 排查时根本分不出是谁。
    ``client_id`` 是这一刻唯一已知的、能归属到具体客户端的值。
    """
    limit = settings.oauth_token_per_minute_per_client
    if limit <= 0:
        return None
    try:
        await RateLimiter().check_bucket("oauth-token-client", client_id, limit)
    except RateLimitError:
        logger.warning("oauth_token_rate_limited", client_id=client_id, limit=limit)
        return JSONResponse(
            status_code=429,
            content={"error": "temporarily_unavailable", "error_description": "Too many requests"},
            headers={**_NO_STORE, "Retry-After": str(_WINDOW_SECONDS)},
        )
    return None


def oauth_error(error: str, description: str, *, status_code: int) -> JSONResponse:
    """RFC 6749 §5.2 的错误响应。

    ``invalid_client`` 用 401（规范要求），其余用 400。不带 ``WWW-Authenticate``：
    那个头是给**受保护资源**的 401 用的，令牌端点不是受保护资源，加上它会让客户端
    以为该去重新走一遍发现流程。
    """
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers=_NO_STORE,
    )


async def _authorization_code_grant(form: Mapping[str, object], client_id: str):
    # 先验证不依赖授权码记录的必填项：协议格式错误不应消耗一次性授权码，
    # 否则客户端只是漏传 resource 也必须让用户重新登录授权。
    requested_resource = str(form.get("resource") or "")
    if not requested_resource:
        raise AuthorizationCodeError("invalid_request", "resource is required")
    record = await redeem_authorization_code(
        code=str(form.get("code") or ""),
        client_id=client_id,
        redirect_uri=str(form.get("redirect_uri") or ""),
        code_verifier=str(form.get("code_verifier") or ""),
    )
    if requested_resource != record.resource:
        # RFC 8707 §2.2：令牌请求可以带上 resource，但必须与授权时请求的一致。
        # 不一致意味着客户端想把它拿到的授权"转卖"给另一个资源，直接拒绝。
        raise AuthorizationCodeError("invalid_target", "resource does not match the authorization request")
    from app.oauth.identity import current_identity

    identity = await current_identity(record.user_id, record.tenant_id)
    if identity is None or identity.auth_version != record.auth_version or identity.role != record.role:
        raise AuthorizationCodeError("invalid_grant", "the resource owner's authorization is no longer valid")
    return await issue_tokens_for_authorization(
        client_id=record.client_id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        role=identity.role,
        auth_version=identity.auth_version,
        email=identity.email,
        scope=record.scope,
        resource=record.resource,
    )


@router.post(TOKEN_PATH, include_in_schema=False)
async def token(request: Request) -> JSONResponse:
    """令牌端点：``authorization_code`` 与 ``refresh_token`` 两种 grant。"""
    form = await request.form()
    client_id = str(form.get("client_id") or "")
    if not client_id:
        return oauth_error("invalid_client", "client_id is required", status_code=401)
    client = await resolve_client(client_id)
    if client is None:
        return oauth_error("invalid_client", "unknown client", status_code=401)

    # 按 client_id 的那一维限流放在这里而不是中间件里：中间件读表单会消费请求体，
    # 下游就拿不到了。这里表单已经解析完，顺手计数不产生第二次读取。
    throttled = await _throttle_client(client.client_id)
    if throttled is not None:
        return throttled

    grant_type = str(form.get("grant_type") or "")
    try:
        if grant_type == "authorization_code":
            issued = await _authorization_code_grant(form, client.client_id)
        elif grant_type == "refresh_token":
            requested_resource = str(form.get("resource") or "")
            if not requested_resource:
                raise TokenError("invalid_request", "resource is required")
            issued = await rotate_refresh_token(
                refresh_token=str(form.get("refresh_token") or ""),
                client_id=client.client_id,
                resource=requested_resource,
            )
        else:
            raise TokenError("unsupported_grant_type", f"unsupported grant_type: {grant_type or '(missing)'}")
    except (AuthorizationCodeError, TokenError) as exc:
        return oauth_error(exc.error, exc.description, status_code=400)

    body = {
        "access_token": issued.access_token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "refresh_token": issued.refresh_token,
    }
    if issued.scope:
        body["scope"] = issued.scope
    return JSONResponse(body, headers=_NO_STORE)


@router.post(REVOCATION_PATH, include_in_schema=False)
async def revoke(request: Request) -> JSONResponse:
    """撤销端点（RFC 7009）。"""
    form = await request.form()
    client_id = str(form.get("client_id") or "")
    if not client_id:
        return oauth_error("invalid_client", "client_id is required", status_code=401)
    client = await resolve_client(client_id)
    if client is None:
        return oauth_error("invalid_client", "unknown client", status_code=401)

    supplied = str(form.get("token") or "")
    if supplied:
        await revoke_token(token=supplied, client_id=client.client_id)
    # RFC 7009 §2.2：令牌未知或已失效时**同样**返回 200。否则这个端点会变成一个
    # "这个令牌存在吗"的探测器。
    return JSONResponse({}, status_code=200, headers=_NO_STORE)
