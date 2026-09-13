"""AS 发现面的守卫：**声明过的每一个端点都必须真的存在**。

为什么需要这条守卫
------------------
``authorization_server_metadata()`` 的契约是"只声明真实存在的端点与能力"。它很容易被
违背，而且违背的方式很隐蔽：加一个 ``introspection_endpoint`` 字段、却忘了把路由
挂上去，本仓库的任何测试都不会红 —— 客户端才会，表现为"授权服务器坏了"，而 AS 的
日志里只有一次 404。

所以这里不逐字段写断言（那会随字段增加而腐化），而是**遍历**文档里所有以
``_endpoint`` 结尾的成员，逐个去应用的路由表里查它是否存在。新增一个端点而忘了
挂载，这条测试当场就红。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.oauth.main import create_oauth_app
from app.oauth.metadata import INTROSPECTION_PATH, JWKS_PATH, METADATA_PATH, authorization_server_metadata


@pytest.fixture()
def as_client() -> TestClient:
    """不进入 ``with`` 块 ⇒ 不触发 lifespan（本文件不碰数据库）。"""
    return TestClient(create_oauth_app())


def test_every_declared_endpoint_is_actually_mounted(as_client: TestClient) -> None:
    """遍历文档里所有 ``*_endpoint``，逐个**发一次请求**确认它真的存在。

    不去读 ``app.routes``：那份结构随框架版本变（当前版本把 ``include_router`` 的结果
    包成 ``_IncludedRouter``，连 ``path`` 都没有），而且"路由表里有"与"能被访问到"是两件
    事 —— 中间可能夹着挂载顺序或前缀问题。断言可观测的东西：``404`` 表示没有这个端点，
    其余任何状态码（200 / 400 / 401 / 403 / 405 / 422）都说明它在那儿。
    """
    declared = authorization_server_metadata()
    documented = {key: value for key, value in declared.items() if key.endswith("_endpoint") and isinstance(value, str)}
    assert documented, "元数据里一个端点都没有 —— 文档本身坏了"

    for key, value in documented.items():
        path = urlsplit(value).path
        response = as_client.get(path)
        assert response.status_code != 404, f"元数据声明了 {key}={value}，但那个路径不存在"


def test_introspection_endpoint_is_declared() -> None:
    """内省端点必须出现在元数据里，否则客户端只能靠硬编码路径。"""
    assert authorization_server_metadata()["introspection_endpoint"].endswith(INTROSPECTION_PATH)


def test_metadata_is_readable_without_credentials(as_client: TestClient) -> None:
    """发现必须匿名可读：读它的时候还没有任何凭据。"""
    response = as_client.get(METADATA_PATH)

    assert response.status_code == 200
    assert response.json()["issuer"] == settings.oauth_issuer


def test_jwks_never_publishes_private_key_material(as_client: TestClient) -> None:
    """JWKS 里出现 ``d``（ECDSA 私钥分量）就等于把签名密钥公开了。"""
    document = as_client.get(JWKS_PATH).json()

    assert document["keys"], "JWKS 为空 —— RS 将无法验签"
    for key in document["keys"]:
        assert set(key) == {"kty", "crv", "x", "y", "kid", "use", "alg"}
        assert "d" not in key
