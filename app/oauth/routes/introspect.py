"""令牌内省端点（RFC 7662）。

它解决什么问题
--------------
access token 是**自包含**的：RS 用 JWKS 本地验签，不需要问 AS。所以内省不是 RS 的
必经之路，它服务的是另外两种场景：

1. **排障**。"客户端说它的令牌无效" —— 需要有人能回答"这把令牌此刻还有效吗、
   是什么 scope、属于哪个客户端"；
2. **撤销的确认**。"撤销之后真的立刻失效了吗" —— 内省给出与 RS 完全相同的判据
   （同一个 ``is_revoked`` 查询），因此它是对撤销链路的直接观测手段。

为什么"只认 client_id"不等于没做认证
------------------------------------
RFC 7662 §2.1 要求调用方经过授权。本 AS 只发放 public client（无 secret），
客户端跑在用户自己的机器上 —— 给它一个 secret 等于把 secret 公开。因此这里能做的
"授权"只有两件事，都很实在：

- 必须是一个**已注册**的 ``client_id``（未注册的直接 401）；
- 被查询的令牌必须**属于**那个客户端（否则回 ``active: false``，见下）。

真正的保护来自被查询的对象本身：``token`` 是一个 256 位随机值或一把签过名的 JWT，
不知道它的人无法通过猜测获得任何信息 —— 这个端点不是"令牌存在性探测器"，
因为探测的前提是已经持有令牌。

为什么不做限流
--------------
DCR 端点做了限流，因为它是**写**端点且无需任何输入即可被滥用（批量注册）。内省是
只读的、且每次调用都需要一个有效的 client_id 与一个令牌；没有它们，调用只会拿到
一个 401 或 ``active: false``，不产生状态。要在这里做限流，需要先有身份维度 ——
而那正是本端点没有的东西。这个取舍记在威胁模型 T-12 里（匿名/半匿名端点的
独立配额属 WP3/WP8）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.oauth.metadata import INTROSPECTION_PATH
from app.oauth.registry import resolve_client
from app.oauth.routes.token import oauth_error
from app.oauth.tokens import introspect_token

router = APIRouter(tags=["oauth-introspect"])

#: 内省响应与令牌响应同属"不得被缓存"的一类：它会泄漏"某个时刻这把令牌是否有效"。
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

#: 可被查询的令牌长度上限。超长输入没有任何合法来源（refresh token 是 43 字符的
#: url-safe base64，access token 是几百字符的 JWT），却会让每次调用多算一次 SHA-256。
#: 超过就直接按"未知令牌"处理（inactive），不报 400 —— 见下面那条注释。
_MAX_TOKEN_LENGTH = 4096


@router.post(INTROSPECTION_PATH, include_in_schema=False)
async def introspect(request: Request) -> JSONResponse:
    """RFC 7662 §2.1 的令牌内省端点。"""
    form = await request.form()
    client_id = str(form.get("client_id") or "")
    if not client_id:
        return oauth_error("invalid_client", "client_id is required", status_code=401)
    client = await resolve_client(client_id)
    if client is None:
        return oauth_error("invalid_client", "unknown client", status_code=401)

    supplied = str(form.get("token") or "")
    if not supplied or len(supplied) > _MAX_TOKEN_LENGTH:
        # 未知令牌回 ``active: false`` 而不是 400：RFC 7662 §2.2 允许这样做，而返回
        # 一个"格式不对"的错误会让本端点变成"这个值看起来像令牌吗"的探测器 ——
        # 与撤销端点必须对未知令牌返回 200 是同一条推理。
        return JSONResponse({"active": False}, headers=_NO_STORE)

    body = await introspect_token(token=supplied, client_id=client.client_id)
    return JSONResponse(body, headers=_NO_STORE)
