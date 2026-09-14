"""授权端点：参数校验、登录、同意页，与授权码的签发。

两条"绝不重定向"的规则
----------------------
参数错误通常应该 302 回 ``redirect_uri`` 并带上 ``error``（RFC 6749 §4.1.2.1）。
但有两处**必须**渲染错误页而不是重定向：

- ``client_id`` 不认识；
- ``redirect_uri`` 与注册值不匹配。

理由很直接：这两条检查的全部意义就是防止授权码被送去攻击者的地址。一边拒绝它、
一边又按它给的地址重定向，等于把检查本身变成了一次开放重定向。RFC 6749 §4.1.2.1
也明确要求此时不得重定向。

表单为什么要回显全部授权参数
----------------------------
登录与同意是两个独立的 POST，之间没有服务端会话态。把参数回显进隐藏字段、在 POST
里**从头重新校验一遍**，比"先在服务端记住这次请求"更安全：任何被篡改的回显值都会在
重新校验时被同一套规则拒绝，而服务端暂存则要额外回答"这份暂存属于谁、活多久、
能否被重放"。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.access.scopes import Scope
from app.config.settings import settings
from app.data.models.oauth import UNBOUND_TENANT
from app.oauth.authorization import SUPPORTED_CODE_CHALLENGE_METHODS, issue_authorization_code
from app.oauth.clients import ClientMetadata, redirect_uri_matches
from app.oauth.html import render_page
from app.oauth.identity import authenticate, revalidate_session
from app.oauth.metadata import AUTHORIZATION_PATH
from app.oauth.registry import bind_dynamic_client_tenant, resolve_client
from app.oauth.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    AsSession,
    issue_session_cookie_value,
    read_session_cookie,
)
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["oauth-authorize"])

LOGIN_PATH = "/oauth/authorize/login"
CONSENT_PATH = "/oauth/authorize/consent"
LOGOUT_PATH = "/oauth/authorize/logout"

#: 会被回显进表单、并因此会在 POST 时被重新校验的授权参数白名单。
#: 用白名单而不是"回显全部"：保证页面里只出现我们知道含义的字段。
_AUTHORIZATION_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
    "resource",
)

#: 同意页上每个 scope 的中文说明。刻意与 ``app/access/scopes.py`` 分开放：
#: 词表是**对外契约**（改动等于破坏已有客户端的授权），而这段文字只是给人看的，
#: 措辞会改。混在一处，早晚有人为了改一句文案而碰到契约。
SCOPE_DESCRIPTIONS: dict[str, str] = {
    Scope.POLICY_READ.value: "检索并阅读你所在单位的 HR 制度与政策原文",
    Scope.CASE_READ.value: "查看你有权访问的 HR 案件上下文",
    Scope.CASE_PROPOSE.value: "起草 HR 案件的处置建议（需人工审批后才会生效）",
    Scope.APPROVAL_READ.value: "查看审批请求的状态",
    Scope.PROFILE_READ.value: "查看当前连接的身份与可用权限范围（不会读取他人数据）",
}


@dataclass(frozen=True)
class AuthorizationRequest:
    """一次已通过校验的授权请求。"""

    client: ClientMetadata
    redirect_uri: str
    scope: str
    state: str
    code_challenge: str
    resource: str
    params: dict[str, str]


class PageError(Exception):
    """必须在页面上展示、且**不得**重定向的错误。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class RedirectError(Exception):
    """应通过 ``redirect_uri`` 回传给客户端的错误（RFC 6749 §4.1.2.1）。"""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


def _append_query(uri: str, extra: Mapping[str, str]) -> str:
    """在 URI 上追加查询参数，保留它原有的查询串。"""
    parts = urlsplit(uri)
    appended = urlencode(list(extra.items()))
    merged = f"{parts.query}&{appended}" if parts.query else appended
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


def _collect_params(source: Mapping[str, object]) -> dict[str, str]:
    return {name: str(source[name]) for name in _AUTHORIZATION_PARAMS if source.get(name) is not None}


def _callback_form_action_origin(redirect_uri: str) -> str | None:
    """把**已通过校验**的 ``redirect_uri`` 归约成 CSP ``form-action`` 的一个源。

    为什么非要它：同意页的表单 POST 之后，服务端 302 回客户端回调；**Chrome 与
    Safari 会把 ``form-action`` 也施加到这次重定向**（Firefox 不会）。``'self'``
    于是把"跳回回调"整体拦掉 —— 症状是点了"授权"页面毫无反应、也不跳转，而服务端
    日志里授权码已经签发。loopback 客户端（RFC 8252）每次都用**随机端口**，回调对
    授权页必然是另一个源，所以必须显式列出这一个。

    只从**已经过 ``redirect_uri_matches`` 校验**的值派生，且只放行这一个源：
    不做通配、不信任请求里的原始字符串（未校验前一律返回 ``None``，见调用方）。
    自定义协议（如 ``workbuddy://``）在 CSP 里写成 scheme 源 ``workbuddy:``。
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    if parts.scheme:
        return f"{parts.scheme}:"
    return None


def _form_action_origins(authorization: AuthorizationRequest) -> list[str]:
    """该授权请求的表单页面需要放行的回调源（最多一个）。"""
    origin = _callback_form_action_origin(authorization.redirect_uri)
    return [origin] if origin else []


def _error_redirect(redirect_uri: str, *, error: str, description: str, state: str) -> str:
    extra = {"error": error, "error_description": description}
    if state:
        # RFC 6749 §4.1.2.1：错误响应同样必须回显 state，否则客户端无法把这次失败
        # 与它发起的那次请求对应起来。
        extra["state"] = state
    return _append_query(redirect_uri, extra)


async def _resolve_client_and_redirect(params: Mapping[str, str]) -> tuple[ClientMetadata, str]:
    """前两步校验：客户端存在，且 ``redirect_uri`` 是它注册过的。

    这两步失败时**不能**重定向 —— 见模块头部。
    """
    client_id = params.get("client_id", "")
    if not client_id:
        raise PageError("请求里缺少 client_id。", "这个参数标识发起授权的应用。")
    client = await resolve_client(client_id)
    if client is None:
        raise PageError("无法识别发起授权的应用。", f"client_id = {client_id}")
    requested = params.get("redirect_uri", "")
    if not requested:
        raise PageError("请求里缺少 redirect_uri。")
    if not redirect_uri_matches(client.redirect_uris, requested):
        raise PageError(
            "回调地址与这个应用注册的不一致。",
            "出于安全考虑，授权码只会送回注册过的地址 —— 不会按请求里给的地址发送。",
        )
    return client, requested


def _validate_authorization_request(
    params: Mapping[str, str], client: ClientMetadata, redirect_uri: str
) -> AuthorizationRequest:
    """其余全部校验。失败时按 RFC 6749 §4.1.2.1 通过 ``redirect_uri`` 回传错误。"""
    if params.get("response_type") != "code":
        raise RedirectError("unsupported_response_type", "only response_type=code is supported")

    challenge = params.get("code_challenge", "")
    method = params.get("code_challenge_method", "S256")
    if not challenge:
        # 本 AS 只签发 public client（无 secret），PKCE 是唯一的持有性证明，
        # 因此它是必填而不是可选。
        raise RedirectError("invalid_request", "code_challenge is required")
    if method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        raise RedirectError("invalid_request", "code_challenge_method must be S256")

    requested_scope = params.get("scope")
    if requested_scope is None or not requested_scope.strip():
        # 未指定 scope 时授予客户端注册时的全部范围。这是安全的：客户端注册范围本身
        # 就是它的上限，而且比"默认拒绝"少一轮来回。
        granted = frozenset(client.scopes)
    else:
        granted = frozenset(part for part in requested_scope.split(" ") if part)
    over_reach = sorted(granted - set(client.scopes))
    if over_reach:
        # 超出客户端注册范围时必须**拒绝**而不是裁剪：裁剪会让客户端以为拿到了，
        # 然后在调用工具时收到一个与授权阶段对不上的错误。
        raise RedirectError("invalid_scope", f"scope not registered for this client: {', '.join(over_reach)}")

    resource = params.get("resource")
    if not resource:
        raise RedirectError("invalid_request", "resource is required")
    if resource != settings.mcp_resource_url:
        # RFC 8707 §2：resource 与令牌 audience 逐字节绑定。本服务只有一个受保护资源，
        # 为别的资源签发的令牌在这里没有任何用处，所以直接拒绝而不是忽略。
        raise RedirectError("invalid_target", "resource must be the canonical MCP resource for this deployment")

    return AuthorizationRequest(
        client=client,
        redirect_uri=redirect_uri,
        scope=" ".join(sorted(granted)),
        state=params.get("state", ""),
        code_challenge=challenge,
        resource=resource,
        params=_collect_params(params),
    )


def _sign_in_response(
    session: AsSession, context: Mapping[str, Any], *, form_action_origins: Sequence[str] = ()
) -> HTMLResponse:
    """渲染同意页并附上会话 cookie。"""
    response = render_page("consent.html", form_action_origins=form_action_origins, **context)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issue_session_cookie_value(session),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        # SameSite=Lax：授权流程是从站外跳进来的，Strict 会让 AS 在这一跳里看不到
        # 自己的 cookie，于是每个用户每次都要重新登录。Lax 仍然挡住了跨站 POST 携带。
        samesite="lax",
        # 本地开发是 http，Secure 会让 cookie 直接不被写入。生产必须为真。
        secure=settings.is_production,
        path="/",
    )
    return response


def _consent_context(request: AuthorizationRequest, session: AsSession) -> dict[str, Any]:
    requested = [item for item in request.scope.split(" ") if item]
    trust = {
        "pre_registered": ("组织管理员已验证", "verified"),
        "cimd": ("客户端身份文档已验证", "verified"),
        "dcr": ("用户自助添加，请确认名称与来源", "unverified"),
    }.get(request.client.registration_source, ("来源未验证", "unverified"))
    can_propose = Scope.CASE_PROPOSE.value in requested
    return {
        "client_name": request.client.client_name,
        "session": session,
        "scopes": [
            {"value": value, "description": SCOPE_DESCRIPTIONS.get(value, "（尚未被任何工具使用的授权范围）")}
            for value in requested
        ],
        "unregistered_scopes": [value for value in requested if value not in SCOPE_DESCRIPTIONS],
        "trust_label": trust[0],
        "trust_tone": trust[1],
        "risk_label": "可发起办理建议（仍需人工审批）" if can_propose else "仅查询与查看",
        "params": request.params,
    }


@router.get(AUTHORIZATION_PATH, include_in_schema=False)
async def authorize(request: Request) -> Response:
    """授权端点：校验参数，然后按会话状态决定展示登录页还是同意页。"""
    params = _collect_params(request.query_params)
    try:
        client, redirect_uri = await _resolve_client_and_redirect(params)
    except PageError as exc:
        return render_page("error.html", status_code=400, message=exc.message, detail=exc.detail)

    try:
        authorization = _validate_authorization_request(params, client, redirect_uri)
    except RedirectError as exc:
        return RedirectResponse(
            _error_redirect(redirect_uri, error=exc.error, description=exc.description, state=params.get("state", "")),
            status_code=302,
        )

    session = read_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if session is not None:
        session = await revalidate_session(session)
    if session is None or session.tenant_id != client.tenant_id:
        # 会话要么不存在，要么属于**另一个租户**（用户先授权了别的客户端）。租户是
        # 会话的边界：acme 租户的登录态不能给缺省租户的客户端签授权码 —— 同意的
        # 必须是"这个租户里的这个人"。回登录页，登录会按当前客户端的租户重新认证。
        return render_page(
            "login.html",
            client_name=client.client_name,
            params=authorization.params,
            tenant_required=client.tenant_id == UNBOUND_TENANT,
            form_action_origins=_form_action_origins(authorization),
        )
    return render_page(
        "consent.html",
        **_consent_context(authorization, session),
        form_action_origins=_form_action_origins(authorization),
    )


async def _revalidate_form(form: Mapping[str, object]) -> tuple[AuthorizationRequest | None, Response | None]:
    """把表单回显的参数当成**全新请求**重新校验一遍。

    这是整套设计里最要紧的一点：回显字段是用户可改的，所以它们不能携带任何信任。
    重新走同一套校验，意味着"篡改回显参数"与"直接构造一个非法授权请求"会遇到
    完全相同的拒绝路径。
    """
    params = _collect_params(form)
    try:
        client, redirect_uri = await _resolve_client_and_redirect(params)
    except PageError as exc:
        return None, render_page("error.html", status_code=400, message=exc.message, detail=exc.detail)
    try:
        return _validate_authorization_request(params, client, redirect_uri), None
    except RedirectError as exc:
        return None, RedirectResponse(
            _error_redirect(redirect_uri, error=exc.error, description=exc.description, state=params.get("state", "")),
            status_code=302,
        )


@router.post(LOGIN_PATH, include_in_schema=False)
async def authorize_login(request: Request) -> Response:
    """处理登录表单，成功后直接进入同意页。"""
    form = await request.form()
    authorization, failure = await _revalidate_form(form)
    if failure is not None or authorization is None:
        assert failure is not None
        return failure

    # 会话租户必须与客户端归属租户一致：登录是按**那个客户端**的租户进行的（见
    # ``identity.authenticate``）。不设这道闸，一个在缺省租户登录的会话就能给
    # acme 租户的客户端签授权码 —— 令牌上的租户成了表单可改的字段。
    tenant_id = authorization.client.tenant_id
    if tenant_id == UNBOUND_TENANT:
        tenant_id = str(form.get("tenant_id") or "").strip()
        if not tenant_id:
            return render_page(
                "login.html",
                status_code=400,
                client_name=authorization.client.client_name,
                params=authorization.params,
                tenant_required=True,
                error="请填写组织标识。",
                form_action_origins=_form_action_origins(authorization),
            )
    session = await authenticate(str(form.get("email") or ""), str(form.get("password") or ""), tenant_id=tenant_id)
    if session is None:
        # 不区分"邮箱不存在"与"密码错误"（见 identity.authenticate）。
        return render_page(
            "login.html",
            status_code=401,
            client_name=authorization.client.client_name,
            params=authorization.params,
            tenant_required=authorization.client.tenant_id == UNBOUND_TENANT,
            error="邮箱或密码不正确。",
            form_action_origins=_form_action_origins(authorization),
        )
    if authorization.client.tenant_id == UNBOUND_TENANT:
        bound = await bind_dynamic_client_tenant(authorization.client.client_id, tenant_id)
        if bound is None:
            return render_page(
                "error.html",
                status_code=409,
                message="这个客户端已经绑定到另一个组织。",
                detail="请在 AI 助手中删除连接后重新添加。",
            )
        authorization = AuthorizationRequest(
            client=bound,
            redirect_uri=authorization.redirect_uri,
            scope=authorization.scope,
            state=authorization.state,
            code_challenge=authorization.code_challenge,
            resource=authorization.resource,
            params=authorization.params,
        )
    return _sign_in_response(
        session, _consent_context(authorization, session), form_action_origins=_form_action_origins(authorization)
    )


@router.post(CONSENT_PATH, include_in_schema=False)
async def authorize_consent(request: Request) -> Response:
    """处理同意/拒绝，签发授权码或回传 ``access_denied``。"""
    form = await request.form()
    authorization, failure = await _revalidate_form(form)
    if failure is not None or authorization is None:
        assert failure is not None
        return failure

    session = read_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if session is not None:
        session = await revalidate_session(session)
    if session is None or session.tenant_id != authorization.client.tenant_id:
        # 会话过期，或它属于另一个租户（见 ``authorize`` 里同一道闸）。回登录页，
        # 而不是默默用"另一个租户的人"的身份签发授权码。
        return render_page(
            "login.html",
            status_code=401,
            client_name=authorization.client.client_name,
            params=authorization.params,
            error="登录状态已过期，请重新登录。",
            form_action_origins=_form_action_origins(authorization),
        )

    if str(form.get("decision") or "") != "allow":
        logger.info("oauth_authorization_denied", client_id=authorization.client.client_id, user_id=session.user_id)
        return RedirectResponse(
            _error_redirect(
                authorization.redirect_uri,
                error="access_denied",
                description="the resource owner denied the request",
                state=authorization.state,
            ),
            status_code=302,
        )

    code = await issue_authorization_code(
        client_id=authorization.client.client_id,
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        role=session.role,
        auth_version=session.auth_version,
        email=session.email,
        redirect_uri=authorization.redirect_uri,
        scope=authorization.scope,
        resource=authorization.resource,
        code_challenge=authorization.code_challenge,
    )
    logger.info("oauth_authorization_granted", client_id=authorization.client.client_id, user_id=session.user_id)
    extra = {"code": code}
    if authorization.state:
        extra["state"] = authorization.state
    return RedirectResponse(_append_query(authorization.redirect_uri, extra), status_code=302)


@router.post(LOGOUT_PATH, include_in_schema=False)
async def authorize_logout(request: Request) -> Response:
    """Switch account without turning the authorization page into a GET logout CSRF."""
    form = await request.form()
    authorization, failure = await _revalidate_form(form)
    if failure is not None or authorization is None:
        assert failure is not None
        return failure
    response = render_page(
        "login.html",
        client_name=authorization.client.client_name,
        params=authorization.params,
        tenant_required=authorization.client.tenant_id == UNBOUND_TENANT,
        form_action_origins=_form_action_origins(authorization),
    )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=settings.is_production, httponly=True)
    return response
