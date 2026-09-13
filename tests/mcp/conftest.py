"""``tests/mcp/`` 共用的假授权服务器与传输层客户端。

为什么放在 conftest 而不是留在某个测试文件里
--------------------------------------------
这份假 AS 同时被两类测试需要：验证"令牌怎么被校验"的，与验证"校验结果怎么被
表达成 HTTP"的。留在前者里，后者就得复制一份 —— 而复制出去的那份会在下一次
改校验逻辑时被漏掉，于是两套测试测的不再是同一件事。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.access import as_tokens
from app.access.scopes import Scope
from app.config.settings import settings
from app.main import create_app
from app.oauth.keys import generate_signing_key


@pytest.fixture(autouse=True)
def _clean_as_document_cache() -> Iterator[None]:
    """缓存是模块级状态，测试之间必须隔离。

    否则"kid 未命中会强制刷新一次"这类断言会失真 —— 上一个测试留下的缓存会让
    这一次的刷新根本不发生，而断言依然通过，于是它什么也没测到。
    """
    as_tokens.reset_document_cache()
    yield
    as_tokens.reset_document_cache()


class FakeAuthorizationServer:
    """一份由测试完全控制的 AS：自己的 ES256 密钥、自己的元数据与 JWKS。"""

    def __init__(self, issuer: str = "https://as.example.test") -> None:
        self.issuer = issuer
        self.signing_key = generate_signing_key()
        self.client_id = "workbuddy"
        self.family_id = str(uuid4())
        self.tenant_id = "tenant-a"
        self.role = "hrbp"
        self.scope = "hrb:policy:read hrb:case:propose"
        #: 假撤销表的返回值
        self.revoked = False
        #: 假客户端注册范围（``None`` 表示"本库不认识这个客户端"）
        self.ceiling: frozenset[Scope] | None = frozenset({Scope.POLICY_READ})
        #: 元数据里声明的 issuer。改成别的值即可模拟 mix-up。
        self.metadata_issuer: str | None = issuer
        #: JWKS 的公钥集合。``None`` 表示"就是这把签名密钥的公钥"。
        self.published_keys: list[dict[str, str]] | None = None
        self.jwks_uri: str | None = f"{issuer}/.well-known/jwks.json"
        #: 置为 True 即可模拟"AS 的文档取不到"（网络故障、AS 未启动）。
        self.documents_unreachable = False
        self.fetch_log: list[str] = []
        self.lookup_log: list[tuple[str, str, str]] = []

    # ---- 假 AS 的 HTTP 面 ------------------------------------------------- #

    @property
    def metadata_url(self) -> str:
        return f"{self.issuer}/.well-known/oauth-authorization-server"

    def _published_keys(self) -> list[dict[str, str]]:
        if self.published_keys is not None:
            return self.published_keys
        return [self.signing_key.public_jwk()]

    async def fetch_json(self, url: str) -> object | None:
        self.fetch_log.append(url)
        if self.documents_unreachable:
            return None
        if url == self.metadata_url:
            document: dict[str, object] = {"jwks_uri": self.jwks_uri}
            if self.metadata_issuer is not None:
                document["issuer"] = self.metadata_issuer
            return document
        if self.jwks_uri is not None and url == self.jwks_uri:
            return {"keys": self._published_keys()}
        return None

    # ---- 假数据库面 ------------------------------------------------------- #

    async def load_registry_state(
        self, *, jti: str, family_id: str, client_id: str
    ) -> tuple[bool, frozenset[Scope] | None]:
        self.lookup_log.append((jti, family_id, client_id))
        return self.revoked, self.ceiling

    # ---- 令牌与装配 ------------------------------------------------------- #

    def claims(self, **overrides: object) -> dict[str, object]:
        now = int(time.time())
        base: dict[str, object] = {
            "iss": self.issuer,
            "aud": settings.mcp_resource_url,
            "sub": "user-1",
            "client_id": self.client_id,
            "scope": self.scope,
            "iat": now,
            "exp": now + 900,
            "jti": str(uuid4()),
            "family_id": self.family_id,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "email": "user-1@example.test",
        }
        base.update(overrides)
        return base

    def token(self, *, kid: str | None = None, typ: str = "at+jwt", **overrides: object) -> str:
        return str(
            jose_jwt.encode(
                self.claims(**overrides),
                self.signing_key.private_pem,
                algorithm="ES256",
                headers={"kid": self.signing_key.kid if kid is None else kid, "typ": typ},
            )
        )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "mcp_authorization_servers", self.issuer)
        monkeypatch.setattr(as_tokens, "_fetch_json", self.fetch_json)
        monkeypatch.setattr(as_tokens, "_load_registry_state", self.load_registry_state)


@pytest.fixture()
def as_server(monkeypatch: pytest.MonkeyPatch) -> FakeAuthorizationServer:
    server = FakeAuthorizationServer()
    server.install(monkeypatch)
    return server


@pytest.fixture()
def mcp_client() -> Iterator[TestClient]:
    """不进入 ``with`` 块 ⇒ 不触发 lifespan（这些测试不需要数据库）。"""
    test_client = TestClient(create_app(), raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()
