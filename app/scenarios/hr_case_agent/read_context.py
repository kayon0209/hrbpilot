"""Tenant context for HR Case agent read tools.

The legacy agent loop (``agent_loop.run_plan``) executes read tools through a
process-global registry whose executor signature is ``(params) -> dict`` — there
is no place to pass the tenant.  Read executors nevertheless need a tenant to
scope retrieval (Milvus + PostgreSQL FTS both filter on ``tenant_id``).

A ContextVar carries the tenant for the duration of one ``run_plan`` call, so a
concurrent run of another tenant can never observe the wrong scope.  The value
is bound by ``run_plan`` and reset in a ``finally`` block.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_tenant: ContextVar[str | None] = ContextVar("hr_case_read_tenant", default=None)

#: 发起这次读调用的**已验证主体**（``McpPrincipal``）。
#:
#: 为什么连主体也要用 ContextVar：``get_my_access_profile`` 返回的是"**这份凭据**
#: 能看见什么"，它必须知道角色的能力、凭据声明的 scope 与客户端上限 —— 这些都不在
#: ``params`` 里，也不该由调用方拼一个 dict 传进来（那会让"谁有权限"重新变成一个
#: 可以伪造的参数）。
#:
#: 与 tenant 共用这个载体而不是另开一条出口，是为了让新工具继续落在**同一条**读
#: 路径上：两个出口各加一个分支的写法，正是当初同一个工具长出两套实现的原因。
_current_principal: ContextVar[object | None] = ContextVar("hr_case_read_principal", default=None)


def bind_read_tenant(tenant_id: str) -> Token:
    """Bind the tenant for the current task; returns a reset token."""
    return _current_tenant.set(tenant_id)


def reset_read_tenant(token: Token) -> None:
    """Restore the previous tenant binding."""
    _current_tenant.reset(token)


def current_read_tenant() -> str | None:
    return _current_tenant.get()


def bind_read_principal(principal: object) -> Token:
    """Bind the verified principal for the current task; returns a reset token."""
    return _current_principal.set(principal)


def reset_read_principal(token: Token) -> None:
    """Restore the previous principal binding."""
    _current_principal.reset(token)


def current_read_principal() -> object | None:
    """The verified principal, or ``None`` when nobody bound one.

    ``None`` 必须被**当作失败**而不是"没有限制"：只有把"没有主体"读成"匿名"，
    才可能出现"拿不到身份于是返回一份默认权限摘要"这种降级。
    """
    return _current_principal.get()
