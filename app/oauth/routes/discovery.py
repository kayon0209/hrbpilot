"""AS 的发现端点：RFC 8414 元数据文档与 JWKS。

两个端点都**必须匿名可读**：客户端拿到它们的时候还没有任何凭据（这正是"发现"
的含义）。要求凭据才能读会让发现流程死锁 —— 要先有令牌才能知道去哪拿令牌。

AS 是独立应用，没有主应用那套认证中间件，因此默认就是匿名的。这里要额外做的是
缓存控制：这两个文档在正常运行时**几乎不变**，但 JWKS 在密钥轮换时会变 ——
所以缓存要短到让轮换能在可接受的时间内生效。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.oauth.keys import public_jwks
from app.oauth.metadata import (
    JWKS_PATH,
    METADATA_PATH,
    authorization_server_metadata,
)

router = APIRouter(tags=["oauth-discovery"])

#: 元数据文档的缓存时长。比 JWKS 更短：文档里的端点集合会随功能开关变化
#: （例如打开 DCR 会多出 ``registration_endpoint``），而那些变化没有版本号可依据。
_METADATA_MAX_AGE_SECONDS = 60
#: JWKS 的缓存时长。轮换窗口期新旧公钥并存，5 分钟足够让新公钥传开，
#: 又不至于让"撤销一把泄漏的密钥"拖延太久。
_JWKS_MAX_AGE_SECONDS = 300


@router.get(METADATA_PATH, include_in_schema=False)
async def authorization_server_metadata_document() -> JSONResponse:
    """RFC 8414 §3 的授权服务器元数据端点。"""
    return JSONResponse(
        authorization_server_metadata(),
        headers={"Cache-Control": f"public, max-age={_METADATA_MAX_AGE_SECONDS}"},
    )


@router.get(JWKS_PATH, include_in_schema=False)
async def json_web_key_set() -> JSONResponse:
    """RFC 7517 §5 的 JWK Set。只含公钥参数。

    资源服务器用这份文档验签。它必须能被匿名读取 —— RS 可能在**没有**任何用户
    上下文的情况下（例如后台任务）校验一个令牌。
    """
    return JSONResponse(
        {"keys": public_jwks()},
        headers={"Cache-Control": f"public, max-age={_JWKS_MAX_AGE_SECONDS}"},
    )
