"""AS 的 FastAPI 应用工厂与独立入口。

独立入口
--------
``uvicorn app.oauth.main:app --port 8001``

与主应用的关系是"同仓、不同进程、不同路由、不同密钥"（ADR-0002 §2/§6）。主应用
只持有公钥：即使它被完全攻破，也签不出任何令牌。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import settings
from app.oauth.keys import active_signing_key
from app.oauth.registry import sync_preconfigured_clients
from app.oauth.routes.authorize import router as authorize_router
from app.oauth.routes.discovery import router as discovery_router
from app.oauth.routes.introspect import router as introspect_router
from app.oauth.routes.register import router as register_router
from app.oauth.routes.token import router as token_router
from app.shared.logger import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动期的两项 **fail fast**。

    1. 主动加载一次签名密钥 —— 格式错误（曲线不对、base64 损坏、生产忘了配）在这里
       就抛出来，而不是等第一个客户走完授权、在"换令牌"那一步才失败。那时错误现场
       在用户浏览器里，服务端只留下一条普通的 token 请求日志。
    2. 把配置里的预注册客户端同步进库 —— 一项写错的 redirect_uri 会让授权码被送到
       一个没人打算信任的地址，必须在启动时就拒绝，而不是等它被用到。
    """
    key = active_signing_key()
    await sync_preconfigured_clients()
    logger.info("oauth_as_started", issuer=settings.oauth_issuer, signing_kid=key.kid)
    yield


def create_oauth_app() -> FastAPI:
    """构建授权服务器应用。"""
    app = FastAPI(
        title="HRBPilot Authorization Server",
        version="0.1.0",
        lifespan=lifespan,
        # 不暴露 OpenAPI / 文档：AS 的对外契约是 RFC 8414 元数据文档，
        # 而不是一份 Swagger UI。多一个自动生成的界面，就多一个会被误当成契约的来源。
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(discovery_router)
    app.include_router(authorize_router)
    app.include_router(token_router)
    app.include_router(introspect_router)
    app.include_router(register_router)
    return app


setup_logging()
app = create_oauth_app()
