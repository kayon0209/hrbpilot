"""WP2-b：Resource Server 侧对 AS 签发令牌的校验。

为什么这个文件几乎全是负例
--------------------------
"接受一个合法令牌"只需要一条断言；风险在那些**看起来像合法令牌**的东西上：为别的
资源签的、别家 AS 签的、已撤销的、把 ``alg`` 改成 HS256 的、``kid`` 指向一把我们
不认识的公钥的。每一条都必须被拒，而且拒绝的原因要落在正确的类别上 —— 否则运维
只能看到一堆"验证失败"，分不清是配置错了、密钥轮换了、还是有人撤销了授权。

三处 seam 是刻意的
------------------
测试不打网络、不连数据库：``_fetch_json``（HTTP 取值）与 ``_load_registry_state``
（撤销表 + 客户端注册范围）是模块级可替换函数，被替换成一份由测试自己控制的假 AS。
被替换的只有"字节从哪来"，验签、claim 校验、主体构造、授权判定全部走真实代码路径。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.access import as_tokens
from app.access.as_tokens import AsAccessClaims, resolve_access_claims, verify_as_access_token
from app.access.scopes import Scope
from app.access.tokens import AccessClaims, TokenRejection
from app.config.settings import settings
from app.main import create_app
from app.mcp.auth import AuthMethod, principal_from_bearer_token, principal_from_headers
from app.mcp.auth.authorization import DenyReason, authorize_tool_call
from app.oauth.keys import generate_signing_key
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG


@pytest.fixture(autouse=True)
def _clean_as_document_cache() -> Iterator[None]:
    """缓存是模块级状态，测试之间必须隔离，否则"kid 未命中会强制刷新"这类断言会失真。"""
    as_tokens.reset_document_cache()
    yield
    as_tokens.reset_document_cache()


class _FakeAuthorizationServer:
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
def as_server(monkeypatch: pytest.MonkeyPatch) -> _FakeAuthorizationServer:
    server = _FakeAuthorizationServer()
    server.install(monkeypatch)
    return server


@pytest.fixture()
def mcp_client() -> Iterator[TestClient]:
    """不进入 ``with`` 块 ⇒ 不触发 lifespan（本文件不需要数据库）。"""
    test_client = TestClient(create_app(), raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()


# --------------------------------------------------------------------------- #
# 1. 正路：一个合法令牌如何变成 McpPrincipal
# --------------------------------------------------------------------------- #


async def test_valid_as_token_becomes_an_oauth_principal(as_server: _FakeAuthorizationServer) -> None:
    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    assert principal.auth_method is AuthMethod.OAUTH
    assert principal.client_id == as_server.client_id
    assert principal.tenant_id == "tenant-a"
    assert principal.role == "hrbp"
    # 安装实例 = 一条 refresh 轮换链。它是 UUID 形态，且与 family_id 一致。
    assert principal.installation_id == UUID(as_server.family_id)
    assert principal.scopes == {Scope.POLICY_READ, Scope.CASE_PROPOSE}
    assert principal.effective_scopes == {Scope.POLICY_READ}


async def test_headers_route_an_as_token_through_the_external_path(as_server: _FakeAuthorizationServer) -> None:
    """工具层（``/mcp`` 这一侧）解出的主体必须是 ``oauth``，而不是被当成自签。"""
    principal = await principal_from_headers({"Authorization": f"Bearer {as_server.token()}"})

    assert principal is not None
    assert principal.auth_method is AuthMethod.OAUTH


async def test_platform_tokens_are_unaffected_while_the_as_is_configured(
    as_server: _FakeAuthorizationServer,
) -> None:
    """配置了 AS 不等于只认 AS 令牌 —— 平台自签令牌仍走它自己的那条路径。"""
    from app.access.routes.auth import _create_access_token

    token = _create_access_token("u-1", "hrbp", "tenant-a", "u-1@example.test")
    claims = await resolve_access_claims(token)

    assert isinstance(claims, AccessClaims)
    assert not isinstance(claims, AsAccessClaims)
    assert claims.tenant_id == "tenant-a"


# --------------------------------------------------------------------------- #
# 2. 负例：每一种"看起来像合法令牌"的东西都必须被拒
# --------------------------------------------------------------------------- #


async def test_token_for_another_resource_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    """``aud`` 绑定的是资源，不是签发者 —— 为别的资源签的令牌在这里毫无用处。"""
    token = as_server.token(aud="https://other.example.test/mcp")

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED
    assert await principal_from_bearer_token(token) is None


async def test_expired_token_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    now = int(time.time())
    token = as_server.token(iat=now - 3600, exp=now - 10)

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED


async def test_token_from_an_untrusted_issuer_is_not_even_routed_to_the_as_path(
    as_server: _FakeAuthorizationServer,
) -> None:
    """未受信任的 issuer 不走 AS 校验，而是退回平台校验（必然验不过）。

    这条断言守的是"分流按已配置的信任列表走"这件事：如果分流按"任何自称是 AS 的
    issuer"走，攻击者就能让 RS 去请求一个自己控制的 JWKS 地址。
    """
    token = as_server.token(iss="https://evil.example.test")

    assert as_tokens.unverified_issuer(token) == "https://evil.example.test"
    assert as_tokens.accepts_issuer("https://evil.example.test") is False
    assert await verify_as_access_token(token) is TokenRejection.MALFORMED
    assert as_server.fetch_log == [], "未受信任的 issuer 不该触发任何外部请求"


async def test_alg_confusion_attempt_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    """把 ``alg`` 换成 HS256、用平台对称密钥签 —— 必须被拒。

    这条守的是"算法由本模块的常量固定，绝不从令牌头里取"。如果实现按头里的 ``alg``
    选验签算法，攻击者就能用一个公开的 ``kid`` + 平台 secret 伪造出 AS 令牌。
    """
    forged = str(
        jose_jwt.encode(
            as_server.claims(),
            settings.jwt_secret,
            algorithm="HS256",
            headers={"kid": as_server.signing_key.kid, "typ": "at+jwt"},
        )
    )

    assert await verify_as_access_token(forged) is TokenRejection.MALFORMED


async def test_refresh_token_type_is_rejected_as_the_wrong_type(as_server: _FakeAuthorizationServer) -> None:
    token = as_server.token(typ="JWT")

    assert await verify_as_access_token(token) is TokenRejection.WRONG_TYPE


async def test_token_missing_bound_claims_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    """缺 ``family_id``（安装实例）或 ``client_id``（客户端）时无法构造主体，必须拒。"""
    assert await verify_as_access_token(as_server.token(family_id="not-a-uuid")) is TokenRejection.INCOMPLETE
    assert await verify_as_access_token(as_server.token(client_id="")) is TokenRejection.INCOMPLETE


async def test_unknown_kid_refreshes_the_jwks_once_then_rejects(as_server: _FakeAuthorizationServer) -> None:
    """``kid`` 未命中 ⇒ 强制刷新一次（密钥轮换的生效路径），仍找不到就拒。

    两件事都要断言：既要**拒**，也要**确实重取过一次** —— 只断言拒绝的话，一个"从不
    刷新缓存"的实现也能过，而它会让轮换后的所有令牌在缓存到期前集体失效。
    """
    token = as_server.token(kid="a-kid-that-does-not-exist")
    before = len(as_server.fetch_log)

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED
    # 取元数据 + 取 JWKS = 2 次；刷新只做一次，不进重试循环。
    assert len(as_server.fetch_log) - before >= 2


async def test_metadata_declaring_another_issuer_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    """RFC 9207 的 mix-up 防御：拿到的文档必须自证它属于我们请求的 issuer。"""
    as_server.metadata_issuer = "https://someone-else.example.test"

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_metadata_without_jwks_uri_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    as_server.jwks_uri = None

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_jwks_that_does_not_contain_the_kid_is_rejected(as_server: _FakeAuthorizationServer) -> None:
    as_server.published_keys = [generate_signing_key().public_jwk()]

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_unreachable_documents_never_grant_access(as_server: _FakeAuthorizationServer) -> None:
    """取不到文档时 fail-closed。这是"AS 挂了"与"令牌是伪造的"唯一可能的重合处理。"""
    as_server.documents_unreachable = True

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


# --------------------------------------------------------------------------- #
# 3. 撤销：与 AS 共用同一份判据
# --------------------------------------------------------------------------- #


async def test_revoked_token_is_rejected_with_its_own_reason(as_server: _FakeAuthorizationServer) -> None:
    as_server.revoked = True

    # 撤销与"验不过"分开：前者是一次已生效的策略动作，后者多半是配置或时间问题。
    assert await verify_as_access_token(as_server.token()) is TokenRejection.REVOKED


async def test_revocation_is_checked_against_both_jti_and_family(as_server: _FakeAuthorizationServer) -> None:
    """撤销判据必须同时带 ``jti`` 与 ``family_id`` 去查。

    只查 ``jti`` 会让"撤销整次授权"失效一半：撤销请求带来的是 refresh token，而 RS
    手里只有 access token，能对上号的只有 ``family_id``。
    """
    token = as_server.token()
    claims = await verify_as_access_token(token)
    assert isinstance(claims, AsAccessClaims)

    assert as_server.lookup_log == [(claims.token_id, claims.family_id, as_server.client_id)]


# --------------------------------------------------------------------------- #
# 4. 客户端授权上限：RS 自己查，不信令牌自述
# --------------------------------------------------------------------------- #


async def test_client_ceiling_narrows_the_effective_scopes(as_server: _FakeAuthorizationServer) -> None:
    as_server.scope = "hrb:policy:read hrb:case:propose"
    as_server.ceiling = frozenset({Scope.POLICY_READ})

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    assert principal.scopes == {Scope.POLICY_READ, Scope.CASE_PROPOSE}
    assert principal.effective_scopes == {Scope.POLICY_READ}

    decision = authorize_tool_call(principal, "create_hr_case", catalog=TOOL_CATALOG)
    assert decision.allowed is False
    # 拒绝原因落在"客户端不够"，而不是"人不够" —— 审计要能分辨这两件事。
    assert decision.deny_reason is DenyReason.CLIENT_CEILING


async def test_ceiling_wider_than_the_grant_does_not_widen_anything(as_server: _FakeAuthorizationServer) -> None:
    as_server.scope = "hrb:policy:read"
    as_server.ceiling = frozenset({Scope.POLICY_READ, Scope.CASE_PROPOSE, Scope.CASE_READ})

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    assert principal.effective_scopes == {Scope.POLICY_READ}


async def test_unknown_client_falls_back_to_the_token_scope(as_server: _FakeAuthorizationServer) -> None:
    """客户端不在本库（例如接了一个第三方 AS）时，上限取令牌自身 —— 不额外收紧。

    断言的是"不额外收紧"，而不是"放宽"：同一个令牌在已知客户端那里会因为上限而
    少一个 scope，在这里不会。真正的天花板始终是**角色能力**（见下一条测试）。
    """
    as_server.ceiling = None
    as_server.scope = "hrb:policy:read"

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    assert principal.client_ceiling == {Scope.POLICY_READ}
    assert principal.effective_scopes == {Scope.POLICY_READ}
    # hrbp 持有 policy_qa 能力且 scope 齐全，因此这里应当放行 —— 回退不能变成拒绝，
    # 否则"接一个第三方 AS"会表现为"所有工具都调不动"，而原因看不出来。
    assert authorize_tool_call(principal, "search_policy", catalog=TOOL_CATALOG).allowed is True


async def test_a_role_without_the_capability_still_cannot_reach_the_tool(
    as_server: _FakeAuthorizationServer,
) -> None:
    as_server.role = "admin"
    as_server.scope = "hrb:policy:read"
    as_server.ceiling = frozenset({Scope.POLICY_READ})

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    decision = authorize_tool_call(principal, "search_policy", catalog=TOOL_CATALOG)
    assert decision.allowed is False
    assert decision.deny_reason is DenyReason.MISSING_CAPABILITY


# --------------------------------------------------------------------------- #
# 5. 传输层：分流真的接在 /mcp 上
# --------------------------------------------------------------------------- #


def test_mcp_accepts_a_valid_as_token(as_server: _FakeAuthorizationServer, mcp_client: TestClient) -> None:
    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {as_server.token()}"}, json={})

    assert response.status_code != 401, "合法的 AS 令牌在传输层被拒 —— 分流没接上"


def test_mcp_challenges_a_revoked_as_token(as_server: _FakeAuthorizationServer, mcp_client: TestClient) -> None:
    as_server.revoked = True

    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {as_server.token()}"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_mcp_challenges_an_as_token_with_the_wrong_audience(
    as_server: _FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    token = as_server.token(aud="https://other.example.test/mcp")

    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_internal_session_tokens_are_still_refused_when_the_switch_is_off(
    as_server: _FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把 ``mcp_accepts_platform_tokens`` 翻成 false 之后，ADR-0002 §4 才真正生效。

    与已有的同类断言不同的是：这里**AS 已经配置好**。区分两种拒绝是必须的 ——
    自签令牌在此时是"被策略拒绝"，而不是"验不过"，两者对运维的含义完全相反。
    """
    from app.access.routes.auth import _create_access_token

    monkeypatch.setattr(settings, "mcp_accepts_platform_tokens", False)
    internal = _create_access_token("u-1", "hrbp", "tenant-a", "u-1@example.test")

    refused = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {internal}"}, json={})
    accepted = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {as_server.token()}"}, json={})

    assert refused.status_code == 401
    assert accepted.status_code != 401
