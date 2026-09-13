"""MCP 调用审计：外部 Agent 每一次工具调用与每一次拒绝的持久化记录。

为什么不复用 ``audit_logs``
----------------------------
``app.data.models.infra.AuditLog`` 是**工作台场景**的审计：它的维度是
scenario / input_summary / output_summary / token 消耗，回答的是"这次问答用了什么"。
外部 Agent 的调用要回答的是另一组问题：

- 是哪个**客户端**打的（不是哪个人 —— 同一个人可能有三个 Agent）；
- 是哪个**安装实例**（撤销要能精确到它）；
- 是拿**哪一把凭据**打的（凭据泄漏时要能列出受影响的行）；
- 结果是哪个**结果码**、为什么被拒。

把这些塞进 ``audit_logs`` 只能靠往 ``input_summary`` 里塞 JSON 或者加一堆可空列；
前者不可查询，后者会把一张已经有明确语义的表变成"什么都装一点"。所以这里单独
一张表，字段按方案 §4.1 第 4 条逐项对应。

为什么这张表**要** RLS
----------------------
与 ``oauth_*`` 四张表相反：那四张表的每一次查询都发生在"还不知道租户是谁"的
时刻，强行开 RLS 会让它们恒返回零行。本表不同 —— 写入时租户**已经确定**
（主体已经建好，否则这次调用早在传输层就 401 了）。所以这里 RLS 是有效的，
而且是**必要的**：审计行里有工具名、对象引用、参数摘要，跨租户可见性是一个
真实的泄漏面，不能靠应用层自觉。

参数只存摘要哈希
----------------
``params_digest`` 是规范化参数的 SHA-256，不是参数原文。理由不是"省空间"：
工具参数里可能有员工姓名、工号、薪酬区间 —— 审计表一旦存原文，它就变成了
第二份（且权限更松的）员工数据副本。存摘要仍然能回答"这两次调用的参数是否
相同"（审批重放检测正需要这个），但不能回答"他查了谁"。

不存的字段
----------
- 完整 token（只存 ``credential_id``，即 jti）；
- 完整检索结果正文（只存结果码与耗时）；
- 请求头、响应体。
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, UUIDPrimaryKey


class McpCallAudit(Base, UUIDPrimaryKey, TenantMixin):
    """一次 MCP 工具调用（含被拒的调用）的审计行。

    被拒的调用也要记：``outcome_code`` 为 ``FORBIDDEN`` / ``AUTH_REQUIRED`` 的行
    才是"有人在试探边界"的证据。只记成功调用等于把攻击面从审计里删掉。
    """

    __tablename__ = "mcp_call_audits"

    # 审计是只追加的，没有 updated_at —— 与 AuditLog 同一条约定。
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # ---- 谁 ----
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    #: 发起调用的客户端。平台自签凭据没有客户端概念，存 "internal"。
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: 安装实例（= 授权会话 family_id）。撤销要能精确到它。平台凭据为 None。
    installation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: 凭据标识：AS 令牌是 jti，平台令牌是令牌自身的 jti（没有则 None）。
    #: **不存令牌本身** —— 见模块文档字符串。
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False)

    # ---- 做了什么 ----
    tool: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    #: 稳定结果码（``app.mcp.contract.ToolOutcome``），不是自由文本。
    outcome_code: Mapped[str] = mapped_column(String(50), nullable=False)
    #: 拒绝原因（``DenyReason``）。只在这里与日志里出现，不进外部响应。
    deny_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- 关联对象（用于串联，不存内容） ----
    #: 规范化参数的 SHA-256。用于"这两次调用参数是否相同"（审批重放检测）。
    params_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 对象引用（如 case_id）。它本身不是敏感数据，且跨租户审计需要它。
    object_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: 审批请求 ID：把"调用"与"它产生的审批"串起来。
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: 请求跟踪 ID：把同一 HTTP 请求里的多次调用串起来。
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    #: 极少量的结构化补充（例如拒绝时的策略版本）。**不得**放参数正文或结果正文。
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<McpCallAudit id={self.id} tool={self.tool} outcome={self.outcome_code}>"
