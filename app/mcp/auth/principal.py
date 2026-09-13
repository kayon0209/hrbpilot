"""MCP 调用主体 —— 「谁、用哪个客户端、被允许到什么程度」的唯一载体。

为什么需要它
------------
外部 Agent 接入前，一次调用的身份就是 JWT 里的 ``(tenant_id, user_id, role)``，
散落在各处的 ``_auth_from_ctx()`` 返回值里（一个裸 dict）。接入后必须区分四件事，
而裸 dict 表达不了：

1. **用户**的身份与角色（能做什么业务）；
2. **客户端/安装实例**的身份（它被授权了哪些 scope、能不能被单独撤销）；
3. **凭据来源**（平台自签 / OAuth / PAT —— 决定上面两项该不该存在）；
4. **有效的 scope 集合**（声明的 scope 与客户端上限的交集）。

把它们收成一个不可变对象，是为了让"身份只能来自已验证凭据"成为**类型上的事实**：
授权函数接受的是 ``McpPrincipal``，不是一个可以随手拼出来的 dict。

两个刻意的取舍
--------------
- 外部凭据（oauth/pat）**必须**同时带 ``installation_id`` 和 ``client_ceiling``，
  否则构造失败。这把"客户端授权上限"从运行时提醒变成结构性约束 —— WP2 的 OAuth
  路径漏填上限会当场报错，而不是静默地把客户端当作没有上限。
- 平台自签凭据的 ``client_id`` 用明确的哨兵值 ``"internal"``，``installation_id``
  保持 ``None``。**不伪造 UUID**：零 UUID 或随机 UUID 会被审计读成"某个真实客户端"，
  从而污染按客户端分组的审计与撤销入口。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.access.scopes import Scope

#: 平台自签凭据的客户端标识。不是注册过的客户端，也不该被当成注册客户端处理。
INTERNAL_CLIENT_ID = "internal"


class AuthMethod(StrEnum):
    """这次调用是拿什么凭据进来的。"""

    OAUTH = "oauth"  # 外部客户端的 access token（授权服务器签发，audience 绑定 /mcp）
    PAT = "pat"  # 兼容性回退：短有效期、scope 受限、可单独撤销
    INTERNAL = "internal"  # 平台自签：网页登录 JWT，无客户端安装绑定


class McpPrincipal(BaseModel):
    """一次 MCP 调用的已验证身份。不可变 —— 授权决策不得就地改写身份。"""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    auth_method: AuthMethod
    client_id: str = Field(min_length=1)
    installation_id: UUID | None = None
    #: 凭据自身声明的 scope。
    scopes: frozenset[Scope] = Field(default_factory=frozenset)
    #: 客户端/安装实例的授权上限。``None`` 仅允许出现在平台自签凭据上。
    client_ceiling: frozenset[Scope] | None = None
    credential_id: UUID | None = None
    token_id: str | None = None

    @model_validator(mode="after")
    def _external_credentials_are_bound_and_capped(self) -> Self:
        if self.auth_method is AuthMethod.INTERNAL:
            return self
        if self.installation_id is None or self.client_ceiling is None:
            raise ValueError("oauth/pat credentials require both installation_id and client_ceiling")
        return self

    @property
    def effective_scopes(self) -> frozenset[Scope]:
        """有效 scope = 凭据声明的 scope ∩ 客户端授权上限。

        平台自签凭据没有上限（``None``），因此等于其声明值；外部凭据必须交出上限，
        交集才可能小于声明值 —— 这正是「用户权限与客户端权限不能混淆」的落点。
        """
        if self.client_ceiling is None:
            return self.scopes
        return self.scopes & self.client_ceiling

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.effective_scopes
