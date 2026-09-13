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

from app.oauth.authorization import AuthorizationCodeError, redeem_authorization_code
from app.oauth.metadata import REVOCATION_PATH, TOKEN_PATH
from app.oauth.registry import resolve_client
from app.oauth.tokens import TokenError, issue_tokens_for_authorization, revoke_token, rotate_refresh_token

router = APIRouter(tags=["oauth-token"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


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
    record = await redeem_authorization_code(
        code=str(form.get("code") or ""),
        client_id=client_id,
        redirect_uri=str(form.get("redirect_uri") or ""),
        code_verifier=str(form.get("code_verifier") or ""),
    )
    requested_resource = form.get("resource")
    if requested_resource and str(requested_resource) != record.resource:
        # RFC 8707 §2.2：令牌请求可以带上 resource，但必须与授权时请求的一致。
        # 不一致意味着客户端想把它拿到的授权"转卖"给另一个资源，直接拒绝。
        raise AuthorizationCodeError("invalid_target", "resource does not match the authorization request")
    return await issue_tokens_for_authorization(
        client_id=record.client_id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        role=record.role,
        email=record.email,
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

    grant_type = str(form.get("grant_type") or "")
    try:
        if grant_type == "authorization_code":
            issued = await _authorization_code_grant(form, client.client_id)
        elif grant_type == "refresh_token":
            issued = await rotate_refresh_token(
                refresh_token=str(form.get("refresh_token") or ""),
                client_id=client.client_id,
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
