"""RFC 9728 Protected Resource Metadata 端点。

为什么这两个端点必须匿名可访问
------------------------------
它们返回的内容是"该去哪里拿令牌"。如果它们自己需要令牌才能读，发现流程就死锁了 ——
客户端要先知道去哪拿令牌，而它知道这件事的唯一途径就是读这两个端点。

「匿名可读」在这里不构成越权面：返回的是**服务的**公共配置（资源标识符、支持的
授权范围、授权服务器地址），不含任何租户数据，也不含任何可被用于横向移动的信息。
真正需要保护的是工具调用，那个边界在 ``app.mcp.auth.authorize_tool_call``。

为什么是两个路径
----------------
RFC 9728 §3.1 规定客户端把 ``/.well-known/oauth-protected-resource`` 插在资源的
host 与 path 之间。本服务的 resource 是 ``<base>/mcp``，所以规范路径是带 path 的
变体；根变体是 RFC 8414 风格的默认发现位置，部分客户端会直接查它。两个都提供，
避免"不同客户端谁能用"取决于它实现的是哪一种 —— 那种差异极难在现场诊断。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.access.resource_metadata import protected_resource_metadata

#: 刻意不带 prefix，也不带 ``/api``：well-known URI 按 RFC 8615 定义在站点根，
#: 客户端不会去 ``/api/.well-known/...`` 找。
router = APIRouter(tags=["oauth-discovery"])

#: 元数据的缓存时长。与 AS 侧元数据的 ``public, max-age=60`` 同一个量级
#: （``app/oauth/routes/discovery.py``）：客户端在发起授权前会读这份文档，缓存让
#: 发现面不至于成为每次授权的固定往返；60 秒是"改了配置（比如换授权服务器地址）
#: 之后，旧文档最坏还能被客户端用多久"的上界 —— 换址是部署级操作，一分钟窗口
#: 完全可控。这里**必须**可缓存而令牌端点**必须**不可缓存：前者只是地址簿，
#: 后者带着凭据。
_METADATA_MAX_AGE_SECONDS = 60


def _metadata_response() -> JSONResponse:
    return JSONResponse(content=protected_resource_metadata(), headers={"Cache-Control": f"public, max-age={_METADATA_MAX_AGE_SECONDS}"})


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata_document() -> JSONResponse:
    """规范路径的根变体。"""
    return _metadata_response()


@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata_document_for_mcp() -> JSONResponse:
    """带 path 的变体 —— resource 为 ``<base>/mcp`` 时客户端的首选位置。"""
    return _metadata_response()
