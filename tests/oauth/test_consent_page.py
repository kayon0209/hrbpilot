"""同意页必须让用户在**看懂**的前提下做决定。

为什么值得测一个 HTML 页面
--------------------------
同意页是整条授权链上唯一由**人**做判断的地方。它前面的每一步都是机器在核对
协议，它后面的每一步都建立在"用户已经同意"这个事实上。如果这一页显示的是
``hrb:policy:read`` 这样的原始串，用户实际上没有做出知情同意 —— 他点的是
"我不认识的东西"。

而这类缺陷不会以任何形式报错：页面渲染成功、200、没有任何日志。所以只能由断言守着。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.access.scopes import Scope
from app.config.settings import settings
from app.oauth.clients import validate_client_metadata
from app.oauth.routes.authorize import SCOPE_DESCRIPTIONS, RedirectError, _validate_authorization_request


def test_every_scope_has_a_chinese_description() -> None:
    """新增 scope 却没有写面向用户的说明时，这条会红。

    漏掉的后果不是页面报错，而是同意页上出现一整行用户看不懂的英文标识符 ——
    而那一刻正是他决定要不要授权的时刻。
    """
    missing = [scope.value for scope in Scope if scope.value not in SCOPE_DESCRIPTIONS]
    assert not missing, f"这些 scope 缺少面向用户的说明：{missing}"


def test_descriptions_are_written_for_people_not_developers() -> None:
    """说明里不得出现内部术语、scope 标识符、字段名。"""
    forbidden = ("hrb:", "scope", "token", "api", "json", "acl", "rbac")
    for value, description in SCOPE_DESCRIPTIONS.items():
        lowered = description.lower()
        for term in forbidden:
            assert term not in lowered, f"{value} 的说明里出现了内部术语 {term!r}：{description}"


def test_descriptions_say_what_the_scope_actually_grants() -> None:
    """每一句都要说清「能做什么」，而不是「这是什么」。

    这条守不住语义漂移的每一种情形（那需要人读），但它挡住了最常见的一种：
    把标识符换个说法重复一遍。要求出现动作词，至少能挡住"仅说明名词"的写法。
    """
    action_markers = ("查看", "检索", "阅读", "起草", "读取")
    for value, description in SCOPE_DESCRIPTIONS.items():
        assert any(marker in description for marker in action_markers), (
            f"{value} 的说明没有说清「能做什么」：{description}"
        )


@pytest.fixture()
def client() -> TestClient:
    from app.oauth.main import create_oauth_app

    return TestClient(create_oauth_app(), raise_server_exceptions=False)


def test_the_page_actually_renders_the_description() -> None:
    """真正渲染一次，确认说明出现在 HTML 里。

    模板用的是 ``scope.description``（属性访问），上下文给的是 dict —— 在 Jinja 里
    靠"先属性后下标"的回退才成立。这是一个隐式依赖：一旦有人改了上下文的键名或
    把 dict 换成别的结构，页面会**静默**渲染成空字符串，没有任何错误、日志或状态码
    变化。只有把渲染结果抓出来看一眼，才能发现。
    """
    from app.oauth.html import render_page

    description = SCOPE_DESCRIPTIONS[Scope.POLICY_READ.value]
    html = render_page(
        "consent.html",
        client_name="示例客户端",
        session=SimpleNamespace(email="user@example.test", role="hrbp"),
        scopes=[{"value": Scope.POLICY_READ.value, "description": description}],
        unregistered_scopes=[],
        params={},
    ).body.decode("utf-8")

    assert description in html, "同意页没有渲染出权限说明 —— 用户看到的是原始标识符"
    assert "hrb:policy:read" in html, "标识符本身也该保留，便于用户与管理员核对"


def test_the_page_says_the_grant_lasts_until_revoked() -> None:
    """用户需要知道"授权之后会怎样"，以及"怎么收回"。"""
    from app.oauth.html import render_page

    html = render_page(
        "consent.html",
        client_name="示例客户端",
        session=SimpleNamespace(email="user@example.test", role="hrbp"),
        scopes=[{"value": Scope.POLICY_READ.value, "description": SCOPE_DESCRIPTIONS[Scope.POLICY_READ.value]}],
        unregistered_scopes=[],
        params={},
    ).body.decode("utf-8")

    assert "撤销" in html, "同意页没有告诉用户授权可以被撤销"


def test_authorization_requires_an_explicit_resource() -> None:
    """RFC 8707 resource 不得由服务端默认补齐。"""
    redirect_uri = "http://127.0.0.1:9000/callback"
    client = validate_client_metadata(
        {
            "redirect_uris": [redirect_uri],
            "scope": Scope.PROFILE_READ.value,
        },
        client_id="explicit-resource-test",
        registration_source="pre_registered",
        tenant_id="default",
    )
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "scope": Scope.PROFILE_READ.value,
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
    }

    with pytest.raises(RedirectError, match="resource is required") as error:
        _validate_authorization_request(params, client, redirect_uri)
    assert error.value.error == "invalid_request"

    accepted = _validate_authorization_request({**params, "resource": settings.mcp_resource_url}, client, redirect_uri)
    assert accepted.resource == settings.mcp_resource_url
