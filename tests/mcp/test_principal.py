"""``McpPrincipal`` 与「角色 → scope」投影的契约回归。

锁定的四件事：

1. **外部凭据必须有客户端上限**：``oauth`` / ``pat`` 主体少了 ``installation_id``
   或 ``client_ceiling`` 就直接构造失败 —— 而不是运行时"忘了判"。
2. **平台自签凭据不伪造客户端身份**：``client_id`` 是明确的哨兵值，``installation_id``
   保持 ``None``，避免审计把平台凭据读成某个真实客户端安装实例。
3. **主体不可变**：授权决策不得就地改写身份。
4. **身份只来自已验证凭据**：``X-Tenant-ID`` 之类的头**不能**改变主体的租户。
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.access.scopes import Scope, scopes_for_role
from app.access.tokens import TokenRejection, read_access_claims
from app.mcp.auth import (
    INTERNAL_CLIENT_ID,
    AuthMethod,
    McpPrincipal,
    principal_from_headers,
    verified_principal,
)


def _token(user_id: str = "u-1", role: str = "hrbp", tenant_id: str = "tenant-a") -> str:
    """用平台自己的签发器造一个真实 access token。

    刻意不手写字节：令牌的 claim 结构（type/jti/aud/iss）由签发器决定，
    测试跟着它走，签发改了这里会一起失败。
    """
    from app.access.routes.auth import _create_access_token

    return _create_access_token(user_id, role, tenant_id, "u-1@example.test")


# --- 1. 外部凭据必须有客户端上限 -------------------------------------------


def test_external_credential_without_installation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="installation_id and client_ceiling"):
        McpPrincipal(
            tenant_id="t-1",
            user_id="u-1",
            role="hrbp",
            auth_method=AuthMethod.OAUTH,
            client_id="workbuddy",
            scopes=frozenset({Scope.POLICY_READ}),
            client_ceiling=frozenset({Scope.POLICY_READ}),
        )


def test_external_credential_without_ceiling_is_rejected() -> None:
    """没有上限的客户端等于没有上限的授权 —— 必须构造失败，而不是默认放行。"""
    with pytest.raises(ValidationError, match="installation_id and client_ceiling"):
        McpPrincipal(
            tenant_id="t-1",
            user_id="u-1",
            role="hrbp",
            auth_method=AuthMethod.PAT,
            client_id="legacy-agent",
            installation_id=uuid.uuid4(),
            scopes=frozenset({Scope.POLICY_READ}),
        )


def test_oauth_credential_with_both_is_accepted() -> None:
    principal = McpPrincipal(
        tenant_id="t-1",
        user_id="u-1",
        role="hrbp",
        auth_method=AuthMethod.OAUTH,
        client_id="workbuddy",
        installation_id=uuid.uuid4(),
        scopes=frozenset({Scope.POLICY_READ, Scope.CASE_PROPOSE}),
        client_ceiling=frozenset({Scope.POLICY_READ}),
    )
    assert principal.effective_scopes == frozenset({Scope.POLICY_READ})


# --- 2. 客户端上限 = 声明 scope ∩ 上限 --------------------------------------


def test_effective_scopes_are_the_intersection_of_grant_and_ceiling() -> None:
    installation = uuid.uuid4()
    principal = McpPrincipal(
        tenant_id="t-1",
        user_id="u-1",
        role="hrbp",
        auth_method=AuthMethod.OAUTH,
        client_id="workbuddy",
        installation_id=installation,
        scopes=frozenset({Scope.POLICY_READ, Scope.CASE_PROPOSE}),
        client_ceiling=frozenset({Scope.CASE_PROPOSE, Scope.APPROVAL_READ}),
    )
    # 上限**收紧**到交集：用户被授予的两个 scope 里，只有 CASE_PROPOSE 在客户端的
    # 允许范围内。反过来（上限比用户权限大）不会放松任何东西。
    assert principal.effective_scopes == frozenset({Scope.CASE_PROPOSE})
    assert principal.has_scope(Scope.CASE_PROPOSE) is True
    assert principal.has_scope(Scope.POLICY_READ) is False


def test_internal_credential_has_no_ceiling() -> None:
    principal = verified_principal(user_id="u-1", role="hrbp", tenant_id="t-1")
    assert principal.auth_method is AuthMethod.INTERNAL
    assert principal.client_id == INTERNAL_CLIENT_ID
    assert principal.installation_id is None
    assert principal.client_ceiling is None
    assert principal.effective_scopes == principal.scopes


# --- 3. 主体不可变 ---------------------------------------------------------


def test_principal_is_frozen() -> None:
    principal = verified_principal(user_id="u-1", role="hrbp", tenant_id="t-1")
    with pytest.raises(ValidationError):
        principal.role = "admin"  # type: ignore[misc]


# --- 4. 身份只来自已验证凭据 -----------------------------------------------


async def test_tenant_header_cannot_override_the_verified_tenant() -> None:
    """``X-Tenant-ID`` 不能改变主体的租户 —— 租户只来自令牌。"""
    principal = await principal_from_headers(
        {
            "Authorization": f"Bearer {_token(tenant_id='tenant-a')}",
            "X-Tenant-ID": "tenant-b",
        }
    )
    assert principal is not None
    assert principal.tenant_id == "tenant-a"


async def test_header_name_is_matched_case_insensitively() -> None:
    """不同客户端的大小写拼法都要认，否则会出现"换了个客户端就登不上"。"""
    for header_name in ("Authorization", "authorization", "AUTHORIZATION"):
        principal = await principal_from_headers({header_name: f"Bearer {_token()}"})
        assert principal is not None, header_name
        assert principal.user_id == "u-1"


async def test_absent_or_malformed_credentials_are_anonymous_not_errors() -> None:
    assert await principal_from_headers(None) is None
    assert await principal_from_headers({}) is None
    # 不是 Bearer 形态
    assert await principal_from_headers({"Authorization": _token()}) is None
    # 空令牌
    assert await principal_from_headers({"Authorization": "Bearer   "}) is None
    # 签名不通过的令牌
    assert await principal_from_headers({"Authorization": "Bearer not-a-jwt"}) is None


async def test_refresh_token_is_not_accepted_as_an_access_credential() -> None:
    """refresh token 不能当作访问凭据 —— 类型判定只有一处，这里守住它。"""
    from app.access.routes.auth import _create_refresh_token

    refresh = _create_refresh_token("u-1", "tenant-a")
    assert read_access_claims(refresh) is TokenRejection.WRONG_TYPE
    assert await principal_from_headers({"Authorization": f"Bearer {refresh}"}) is None


# --- 5. 角色 → scope 投影 --------------------------------------------------


def test_unknown_or_empty_role_gets_no_scopes() -> None:
    """认不出角色时不能退化成"给点基础权限"，要与 capabilities 一样 fail-closed。"""
    assert scopes_for_role(None) == frozenset()
    assert scopes_for_role("") == frozenset()
    assert scopes_for_role("nobody") == frozenset()


def test_platform_admin_holds_no_business_scope() -> None:
    """平台管理员不继承 HR 业务权限（既有 RBAC 设计），scope 侧必须一致。"""
    assert scopes_for_role("admin") == frozenset({Scope.PROFILE_READ})


def test_policy_and_case_roles_hold_the_scopes_their_tools_need() -> None:
    hrbp = scopes_for_role("hrbp")
    assert {Scope.POLICY_READ, Scope.CASE_PROPOSE} <= hrbp
    assert Scope.POLICY_READ in scopes_for_role("employee")
    assert Scope.CASE_PROPOSE not in scopes_for_role("employee")
