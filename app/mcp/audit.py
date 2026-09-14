"""MCP 调用审计的写入口。

为什么单独一个模块
------------------
审计的**写入条件**比"成功了记一笔"复杂：被拒的调用、因客户端上限被拒的调用、
产生审批的调用，都要记，而且各自带不同的关联字段。把这些判断散在调用点里，
漏掉某一类的概率很高 —— 而漏掉的那一类往往正是需要审计的那一类
（"有人在试探边界"只出现在被拒的行里）。

审计失败的两种语义
----------------------
读操作和已拒绝的请求采用**可用性优先**：审计失败会记 error 日志，但不改变原调用结果。
会产生待执行写操作的审批则采用**安全性优先**：审计行与审批记录在同一事务内写入，
审计失败就整体回滚，不留下无法追溯的写入授权。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.mcp_audit import McpCallAudit
from app.mcp.auth.principal import McpPrincipal
from app.shared.logger import get_logger

logger = get_logger(__name__)


def params_digest(params: dict[str, Any] | None) -> str | None:
    """规范化参数的 SHA-256 十六进制摘要。**不存参数原文。**

    工具参数里可能有员工姓名、工号、薪酬区间。审计表一旦存原文，它就变成了
    第二份（且通常权限更松的）员工数据副本 —— 那是一个比"审计不完整"糟得多的
    结果。存摘要仍然能回答"这两次调用的参数是否相同"，而审批重放检测需要的
    正是这个（方案 §WP7：绑定参数哈希、防重放）。

    ``sort_keys`` 是必须的：字典顺序在 Python 里稳定，但跨进程、跨版本不保证
    与插入顺序一致。不排序会让"同一组参数"在不同调用里算出不同摘要，
    重放检测就失效了。
    """
    if not params:
        return None
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_mcp_call(
    principal: McpPrincipal | None,
    *,
    tool: str,
    outcome_code: str,
    deny_reason: str | None = None,
    latency_ms: int | None = None,
    params: dict[str, Any] | None = None,
    object_ref: str | None = None,
    approval_id: str | None = None,
    trace_id: str | None = None,
    detail: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
    required: bool = False,
) -> str | None:
    """写一行 MCP 调用审计，返回行 ID（写失败返回 ``None``）。

    ``principal`` 为 ``None`` 表示匿名调用 —— 也会被记录，只是租户与用户两列
    留空。匿名调用恰恰是需要留痕的那一类。
    """
    # 整段包在 try 里，**包括行构造**。只把数据库操作包起来的话，行构造里任何
    # 一处类型问题（某个字段突然变成不可序列化的值）都会把异常抛给调用方 ——
    # 于是一次审计故障变成一次 500，而 500 在传输层意味着"这次拒绝没生效"。
    # 调用方不应该需要知道审计会不会抛异常。
    try:
        row = McpCallAudit(
            tenant_id=principal.tenant_id if principal else "",
            user_id=principal.user_id if principal else "",
            role=principal.role if principal else "",
            client_id=principal.client_id if principal else "anonymous",
            installation_id=str(principal.installation_id) if principal and principal.installation_id else None,
            credential_id=principal.token_id if principal else None,
            auth_method=principal.auth_method.value if principal else "none",
            tool=tool,
            outcome_code=outcome_code,
            deny_reason=deny_reason,
            latency_ms=latency_ms,
            params_digest=params_digest(params),
            object_ref=object_ref,
            approval_id=approval_id,
            trace_id=trace_id,
            detail=json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None,
        )
        if session is not None:
            session.add(row)
            await session.flush()
            return row.id

        from app.data.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            session.info["tenant_id"] = row.tenant_id
            session.add(row)
            await session.commit()
            return row.id
    except Exception as exc:
        logger.error(
            "mcp_audit_persist_failed",
            tool=tool,
            outcome_code=outcome_code,
            tenant_id=principal.tenant_id if principal else "",
            error=str(exc),
        )
        if required:
            raise
        return None
