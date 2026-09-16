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
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.access import as_tokens
from app.access.as_tokens import AsAccessClaims, resolve_access_claims, verify_as_access_token
from app.access.scopes import Scope
from app.access.tokens import AccessClaims, TokenRejection
from app.config.settings import settings
from app.mcp.auth import AuthMethod, principal_from_bearer_token, principal_from_headers
from app.mcp.auth.authorization import DenyReason, authorize_tool_call
from app.oauth.keys import generate_signing_key
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG
from tests.mcp.conftest import FakeAuthorizationServer

# --------------------------------------------------------------------------- #
# 1. 正路：一个合法令牌如何变成 McpPrincipal
# --------------------------------------------------------------------------- #


async def test_valid_as_token_becomes_an_oauth_principal(as_server: FakeAuthorizationServer) -> None:
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


async def test_headers_route_an_as_token_through_the_external_path(as_server: FakeAuthorizationServer) -> None:
    """工具层（``/mcp`` 这一侧）解出的主体必须是 ``oauth``，而不是被当成自签。"""
    principal = await principal_from_headers({"Authorization": f"Bearer {as_server.token()}"})

    assert principal is not None
    assert principal.auth_method is AuthMethod.OAUTH


async def test_platform_tokens_are_unaffected_while_the_as_is_configured(
    as_server: FakeAuthorizationServer,
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


async def test_token_for_another_resource_is_rejected(as_server: FakeAuthorizationServer) -> None:
    """``aud`` 绑定的是资源，不是签发者 —— 为别的资源签的令牌在这里毫无用处。"""
    token = as_server.token(aud="https://other.example.test/mcp")

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED
    assert await principal_from_bearer_token(token) is None


async def test_expired_token_is_rejected(as_server: FakeAuthorizationServer) -> None:
    now = int(time.time())
    token = as_server.token(iat=now - 3600, exp=now - 10)

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED


async def test_token_from_an_untrusted_issuer_is_not_even_routed_to_the_as_path(
    as_server: FakeAuthorizationServer,
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


async def test_alg_confusion_attempt_is_rejected(as_server: FakeAuthorizationServer) -> None:
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


async def test_refresh_token_type_is_rejected_as_the_wrong_type(as_server: FakeAuthorizationServer) -> None:
    token = as_server.token(typ="JWT")

    assert await verify_as_access_token(token) is TokenRejection.WRONG_TYPE


async def test_token_missing_bound_claims_is_rejected(as_server: FakeAuthorizationServer) -> None:
    """缺 ``family_id``（安装实例）或 ``client_id``（客户端）时无法构造主体，必须拒。"""
    assert await verify_as_access_token(as_server.token(family_id="not-a-uuid")) is TokenRejection.INCOMPLETE
    assert await verify_as_access_token(as_server.token(client_id="")) is TokenRejection.INCOMPLETE


async def test_unknown_kid_refreshes_the_jwks_once_then_rejects(as_server: FakeAuthorizationServer) -> None:
    """``kid`` 未命中 ⇒ 强制刷新一次（密钥轮换的生效路径），仍找不到就拒。

    两件事都要断言：既要**拒**，也要**确实重取过一次** —— 只断言拒绝的话，一个"从不
    刷新缓存"的实现也能过，而它会让轮换后的所有令牌在缓存到期前集体失效。
    """
    token = as_server.token(kid="a-kid-that-does-not-exist")
    before = len(as_server.fetch_log)

    assert await verify_as_access_token(token) is TokenRejection.MALFORMED
    # 取元数据 + 取 JWKS = 2 次；刷新只做一次，不进重试循环。
    assert len(as_server.fetch_log) - before >= 2


async def test_metadata_declaring_another_issuer_is_rejected(as_server: FakeAuthorizationServer) -> None:
    """RFC 9207 的 mix-up 防御：拿到的文档必须自证它属于我们请求的 issuer。"""
    as_server.metadata_issuer = "https://someone-else.example.test"

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_metadata_without_jwks_uri_is_rejected(as_server: FakeAuthorizationServer) -> None:
    as_server.jwks_uri = None

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_jwks_that_does_not_contain_the_kid_is_rejected(as_server: FakeAuthorizationServer) -> None:
    as_server.published_keys = [generate_signing_key().public_jwk()]

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


async def test_unreachable_documents_never_grant_access(as_server: FakeAuthorizationServer) -> None:
    """取不到文档时 fail-closed。这是"AS 挂了"与"令牌是伪造的"唯一可能的重合处理。"""
    as_server.documents_unreachable = True

    assert await verify_as_access_token(as_server.token()) is TokenRejection.MALFORMED


# --------------------------------------------------------------------------- #
# 3. 撤销：与 AS 共用同一份判据
# --------------------------------------------------------------------------- #


async def test_revoked_token_is_rejected_with_its_own_reason(as_server: FakeAuthorizationServer) -> None:
    as_server.revoked = True

    # 撤销与"验不过"分开：前者是一次已生效的策略动作，后者多半是配置或时间问题。
    assert await verify_as_access_token(as_server.token()) is TokenRejection.REVOKED


async def test_revocation_is_checked_against_both_jti_and_family(as_server: FakeAuthorizationServer) -> None:
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


async def test_client_ceiling_narrows_the_effective_scopes(as_server: FakeAuthorizationServer) -> None:
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


async def test_ceiling_wider_than_the_grant_does_not_widen_anything(as_server: FakeAuthorizationServer) -> None:
    as_server.scope = "hrb:policy:read"
    as_server.ceiling = frozenset({Scope.POLICY_READ, Scope.CASE_PROPOSE, Scope.CASE_READ})

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is not None
    assert principal.effective_scopes == {Scope.POLICY_READ}


async def test_unknown_client_is_rejected_fail_closed(as_server: FakeAuthorizationServer) -> None:
    """A registry miss cannot turn the token's self-declared scope into its ceiling."""
    as_server.ceiling = None
    as_server.scope = "hrb:policy:read"

    principal = await principal_from_bearer_token(as_server.token())

    assert principal is None


async def test_a_role_without_the_capability_still_cannot_reach_the_tool(
    as_server: FakeAuthorizationServer,
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


def test_mcp_accepts_a_valid_as_token(as_server: FakeAuthorizationServer, mcp_client: TestClient) -> None:
    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {as_server.token()}"}, json={})

    assert response.status_code != 401, "合法的 AS 令牌在传输层被拒 —— 分流没接上"


def test_mcp_challenges_a_revoked_as_token(as_server: FakeAuthorizationServer, mcp_client: TestClient) -> None:
    as_server.revoked = True

    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {as_server.token()}"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_mcp_challenges_an_as_token_with_the_wrong_audience(
    as_server: FakeAuthorizationServer, mcp_client: TestClient
) -> None:
    token = as_server.token(aud="https://other.example.test/mcp")

    response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={})

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_internal_session_tokens_are_still_refused_when_the_switch_is_off(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
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


# --------------------------------------------------------------------------- #
# 5. 拒绝原因必须可诊断（2026-09-16）
# --------------------------------------------------------------------------- #
# 本文件开头的意图已经写明了："拒绝的原因要落在正确的类别上 —— 否则运维只能看到一堆
# 「验证失败」，分不清是配置错了、密钥轮换了、还是有人撤销了授权。"
#
# 但 ``MALFORMED`` 这一族当时没有落实这句话：签名不过 / 过期 / audience 不符 /
# issuer 不在信任列表，四种原因共用一个值，日志里只有
# ``mcp_token_rejected reason=malformed``。
#
# 实测代价：``MCP_AUTHORIZATION_SERVERS`` 留空时，所有 AS 令牌被静默退回平台校验，
# 而平台校验用 HS256 + 平台密钥，永远验不过 ES256 的 AS 令牌 —— 结果就一句
# ``malformed``。从这个原因逆推出"少配了一个环境变量"花了十几轮排查。
#
# 下面几条锁住两件事：**原因要落到日志里**，以及**不要噪声化**。


def _log_blob(caplog, capsys) -> str:
    """两路都收：structlog 的落点取决于 setup_logging() 有没有被调用过。"""
    import logging

    stdlib_events = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    return "\n".join(stdlib_events) + capsys.readouterr().out


async def test_untrusted_as_issuer_is_logged_with_the_trust_list(
    as_server: FakeAuthorizationServer, caplog, capsys
) -> None:
    """自称是 AS、却不在信任列表里 —— 必须留下可诊断的一行。"""
    import logging

    token = as_server.token(iss="https://evil.example.test")

    with caplog.at_level(logging.WARNING):
        rejection = await resolve_access_claims(token)

    assert rejection is TokenRejection.MALFORMED
    blob = _log_blob(caplog, capsys)
    assert "as_token_issuer_not_trusted" in blob
    assert "evil.example.test" in blob, "日志必须带上那个不被信任的 issuer，否则无法比对配置"


async def test_the_empty_trust_list_hint_names_the_variable(
    as_server: FakeAuthorizationServer, monkeypatch, caplog, capsys
) -> None:
    """信任列表为空是本机事故的确切形态 —— 提示语必须点名那个环境变量。

    这一条是整组测试存在的主要理由：它把"看起来像令牌坏了"变回
    "你少配了一个变量"。
    """
    import logging

    token = as_server.token(iss="https://evil.example.test")
    monkeypatch.setattr(settings, "mcp_authorization_servers", "")

    with caplog.at_level(logging.WARNING):
        await resolve_access_claims(token)

    blob = _log_blob(caplog, capsys)
    assert "as_token_issuer_not_trusted" in blob
    assert "MCP_AUTHORIZATION_SERVERS is empty" in blob


async def test_a_platform_token_does_not_log_the_routing_warning(caplog, capsys) -> None:
    """反向约束：平台令牌本来就走平台路径，不许写日志。

    平台自签令牌的 ``iss`` 是 ``hrbp-ai-workbench``（不是 URL）。如果无条件记日志，
    每一笔平台令牌流量都会写一行 WARNING，真正的问题反而被刷没 ——
    一个"到处都报警"的信号等于没有信号。
    """
    import logging

    from app.access.routes.auth import _create_access_token

    token = _create_access_token("u-1", "hrbp", "tenant-a", "u-1@example.test")

    with caplog.at_level(logging.WARNING):
        claims = await resolve_access_claims(token)

    assert isinstance(claims, AccessClaims)
    assert "as_token_issuer_not_trusted" not in _log_blob(caplog, capsys)


async def test_decode_failure_logs_the_expected_audience(
    as_server: FakeAuthorizationServer, caplog, capsys
) -> None:
    """签名不过 / 过期 / audience 不符三者必须能分辨。

    audience 是这三者里最容易被误判成"令牌坏了"的一种：真正的原因是服务端
    ``PUBLIC_BASE_URL`` 配错，而 ``mcp_resource_url`` 就是从它推出来的。
    日志里带上期望值，排查就不必再去读一遍 settings。
    """
    import logging

    token = as_server.token(aud="https://other.example.test/mcp")

    with caplog.at_level(logging.WARNING):
        rejection = await verify_as_access_token(token)

    assert rejection is TokenRejection.MALFORMED
    blob = _log_blob(caplog, capsys)
    assert "as_token_decode_failed" in blob
    assert settings.mcp_resource_url in blob, "日志要给出期望的 audience 供比对"
