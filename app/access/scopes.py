"""OAuth scope 词表，以及「角色 → scope」的投影。

为什么需要它
------------
在引入外部 AI Agent 之前，调用者的能力只由一个维度决定：**角色**
（``app/access/middleware/rbac.py`` 的 ``ROLE_CAPABILITIES``）。外部 Agent 接入后
多出三个维度 —— 授权服务器签发的 scope、客户端安装实例的授权上限、对象级 ACL ——
方案要求最终权限是它们的**交集**。

交集要成立，前提是四个维度有各自独立、可表达的取值。role 侧已经有了；这里补上
scope 侧，并且只放**面向业务能力**的少量稳定取值（不把每个工具变成一个 scope，
否则 scope 会退化成工具名的副本，客户端每次加工具都要重新授权）。

``scopes_for_role`` 是给**平台自签凭据**（网页登录 JWT）用的投影：这类凭据没有
客户端和授权服务器，它的 scope 只能由角色能力推导。它是恒等映射（角色能力 → scope
→ 再与角色能力相交仍是角色能力），因此不改变任何既有行为；它的作用是让交集
**机制**在 WP2 的 OAuth 路径到来之前就已经在位并被测试覆盖，而不是留到那时才现搭。
"""

from __future__ import annotations

from enum import StrEnum

from app.access.middleware.rbac import ROLE_CAPABILITIES


class Scope(StrEnum):
    """首版 scope 词表。取值是**对外契约**，改名等于破坏已有客户端的授权。"""

    POLICY_READ = "hrb:policy:read"
    CASE_READ = "hrb:case:read"
    CASE_PROPOSE = "hrb:case:propose"
    APPROVAL_READ = "hrb:approval:read"
    PROFILE_READ = "hrb:profile:read"


#: 角色能力 → scope。左侧取值必须真实存在于 ``ROLE_CAPABILITIES``（有测试守着）。
#:
#: ``hrb:case:read`` / ``hrb:approval:read`` 目前**还没有工具消费**（案件上下文与
#: 审批状态工具属于后续工作包）。它们先登记在这里，是为了让 WP7 新增工具时不再
#: 需要改一次 scope 词表和一次角色投影 —— 词表是外部契约，越晚改代价越大。
CAPABILITY_SCOPES: dict[str, frozenset[Scope]] = {
    "policy_qa": frozenset({Scope.POLICY_READ}),
    "hr_case": frozenset({Scope.CASE_READ, Scope.CASE_PROPOSE, Scope.APPROVAL_READ}),
    "work_summary": frozenset({Scope.CASE_PROPOSE, Scope.APPROVAL_READ}),
}

#: 任何已认证主体都持有的 scope。读取"我是谁、我有什么权限"不构成业务数据访问。
BASELINE_SCOPES: frozenset[Scope] = frozenset({Scope.PROFILE_READ})


def parse_scopes(raw: str) -> frozenset[Scope]:
    """空格分隔的 scope 串 → 集合；**认不出的取值被丢弃**。

    RFC 6749 §3.3 的形式（单空格分隔、大小写敏感）。输入来自外部数据 —— 令牌的
    ``scope`` claim、客户端注册元数据 —— 所以这里不能抛异常：一个拼错的 scope 的
    正确后果是"它不授予任何东西"，而不是让整个校验链路 500。丢弃是 fail-closed 方向
    （认不出 ⇒ 没有权限），与 ``scopes_for_role`` 对未知角色的处理一致。

    反过来，如果将来某个 scope 从词表里被删除，用旧词表签出的令牌会因为这一行而
    少一个 scope，而不是构造失败 —— 这是想要的：令牌应当因权限收缩而"部分失效"，
    而不是变成一把谁都验不过的废纸。
    """
    parsed: set[Scope] = set()
    for item in raw.split(" "):
        if not item:
            continue
        try:
            parsed.add(Scope(item))
        except ValueError:
            continue
    return frozenset(parsed)


def scopes_for_role(role: str | None) -> frozenset[Scope]:
    """把角色能力投影成 scope。

    未知或空角色返回**空集**（fail-closed），与 ``capabilities_for_role`` 一致 ——
    不能因为"认不出这个角色"就退化成"给它基础 scope"。
    """
    if not role:
        return frozenset()
    capabilities = ROLE_CAPABILITIES.get(role)
    if capabilities is None:
        return frozenset()
    projected: set[Scope] = set(BASELINE_SCOPES)
    for capability in capabilities:
        projected |= CAPABILITY_SCOPES.get(capability, frozenset())
    return frozenset(projected)
