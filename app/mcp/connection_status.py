"""用户自己的「连接状态」：实例 + 最近调用 + 一句可读结论。

与 ``/capabilities`` 的分工
--------------------------
``/capabilities`` 回答"**这个部署能做什么**"—— 静态能力清单（工具表、套餐、模板）。
本模块回答"**我的助手现在怎么样**"—— 动态运行状态（最近调用、失败、近七天用量）。
两者的变化频率和缓存策略都不同，混进同一个响应里会让"改能力清单"和"查最近调用"互相牵动。

数据从哪来
----------
不改表、不加字段：调用审计（``mcp_call_audits``）本来就记了 ``installation_id`` /
``tool`` / ``outcome_code`` / ``latency_ms``，只是此前没被用户面读过。连接管理中心要的
"最近使用""最近异常"全在里面。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.mcp_audit import McpCallAudit
from app.mcp.installations import list_installations

#: 视为**出问题**的终态。刻意不含 CANCELLED / EXPIRED —— 那是用户取消或草稿超期，
#: 属于正常终态；把它们报成"异常"会让连接管理中心每天都亮红灯。
FAILURE_OUTCOMES: tuple[str, ...] = ("AUTH_REQUIRED", "FORBIDDEN", "FAILED")

#: 视为**通道通了**的终态（有产出、或在等一个人做下一步）。
OK_OUTCOMES: tuple[str, ...] = (
    "FOUND",
    "NO_EVIDENCE",
    "SUCCEEDED",
    "AWAITING_APPROVAL",
    "AWAITING_CONFIRMATION",
    "INPUT_REQUIRED",
    "RUNNING",
)

#: 「近七天用量」的窗口。
_USAGE_WINDOW_DAYS = 7


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


async def _call_summary(
    session: AsyncSession,
    tenant_id: str,
    family_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """每个安装实例的：最近一次调用、最近一次**失败**、近七天调用数。

    ``DISTINCT ON`` 只取每组的首行（已按时间倒序），比"取全部再在 Python 里分组"少读
    几十倍的行 —— 审计表是随调用增长的那张表，不该为了看一行数据把它全捞出来。
    """
    if not family_ids:
        return {}

    scope = (
        McpCallAudit.tenant_id == tenant_id,
        McpCallAudit.installation_id.in_(family_ids),
    )

    latest_rows = (
        await session.execute(
            select(
                McpCallAudit.installation_id,
                McpCallAudit.tool,
                McpCallAudit.outcome_code,
                McpCallAudit.latency_ms,
                McpCallAudit.created_at,
            )
            .where(*scope)
            .order_by(McpCallAudit.installation_id, McpCallAudit.created_at.desc())
            .distinct(McpCallAudit.installation_id)
        )
    ).all()

    failure_rows = (
        await session.execute(
            select(
                McpCallAudit.installation_id,
                McpCallAudit.tool,
                McpCallAudit.outcome_code,
                McpCallAudit.deny_reason,
                McpCallAudit.created_at,
            )
            .where(*scope, McpCallAudit.outcome_code.in_(FAILURE_OUTCOMES))
            .order_by(McpCallAudit.installation_id, McpCallAudit.created_at.desc())
            .distinct(McpCallAudit.installation_id)
        )
    ).all()

    since = datetime.now(UTC) - timedelta(days=_USAGE_WINDOW_DAYS)
    count_rows = (
        await session.execute(
            select(McpCallAudit.installation_id, func.count())
            .where(*scope, McpCallAudit.created_at >= since)
            .group_by(McpCallAudit.installation_id)
        )
    ).all()

    counts = {str(family_id): int(count) for family_id, count in count_rows}

    summary: dict[str, dict[str, Any]] = {}
    for family_id, tool, outcome_code, latency_ms, created_at in latest_rows:
        key = str(family_id)
        summary.setdefault(key, {})["last_call"] = {
            "tool": str(tool),
            "outcome": str(outcome_code),
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "at": _iso(created_at),
        }
    for family_id, tool, outcome_code, deny_reason, created_at in failure_rows:
        key = str(family_id)
        summary.setdefault(key, {})["last_failure"] = {
            "tool": str(tool),
            "outcome": str(outcome_code),
            "deny_reason": str(deny_reason) if deny_reason else None,
            "at": _iso(created_at),
        }
    for key, value in summary.items():
        value["calls_7d"] = counts.get(key, 0)
    return summary


def _check_summary(installations: list[dict[str, Any]], *, tool_count: int) -> dict[str, Any]:
    """结构化结论：**事实在后端，措辞在前端**。

    这里刻意不拼"已接上 2 个（Codex、WorkBuddy），最近调用成功"这样的句子：句子里有助手名，
    而 ``client_id → 人看得懂的名字`` 的映射在前端（只有那里知道该叫 "Codex" 还是"AI 助手"）。
    在后端再写一份映射，就是同一事实的第二份来源 —— 改了前端没改后端，结论里的名字会先错。
    所以这里只给 code 与计数，前端据此出文案。
    """
    live = [item for item in installations if item["status"] == "active"]
    with_calls = [item for item in live if item.get("last_call")]
    failing = [item for item in live if item.get("last_failure")]
    latest = max((item["last_call"] for item in with_calls), key=lambda call: call["at"], default=None)

    if not installations:
        code = "no_installations"
    elif not live:
        code = "all_inactive"
    elif failing:
        code = "recent_failure"
    elif latest is None:
        # 接上了但一次都没调过 —— 通道还没被证明过，不能算"健康"。
        code = "no_calls_yet"
    else:
        code = "healthy"

    return {
        "code": code,
        "installs": len(installations),
        "live": len(live),
        "failing": len(failing),
        "tool_count": tool_count,
        "latest_call": latest,
    }


async def build_my_connections(
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
    *,
    tool_count: int,
    hidden_tool_count: int,
) -> dict[str, Any]:
    """组装「我的连接」：实例 + 每个实例的调用情况 + 一份结构化结论。"""
    records = await list_installations(session, tenant_id, user_id=user_id)
    family_ids = [record.family_id for record in records]
    calls = await _call_summary(session, tenant_id, family_ids)

    installations: list[dict[str, Any]] = []
    for record in records:
        call = calls.get(record.family_id, {})
        active = record.revoked_at is None and record.active_refresh_tokens > 0
        last_failure = call.get("last_failure")
        last_call = call.get("last_call")
        # 「失败是否已被后续成功覆盖」只看时间先后：失败之后有过一次成功到达，
        # 就不该继续把它显示成当前的问题（否则用户会去排查一个已经好了的故障）。
        recovered = bool(
            last_call
            and last_failure
            and last_call.get("at")
            and last_failure.get("at")
            and last_call["at"] > last_failure["at"]
            and last_call.get("outcome") in OK_OUTCOMES
        )
        installations.append(
            {
                **record.model_dump(mode="json"),
                "status": "active" if active else "revoked",
                "last_call": last_call,
                "last_failure": None if recovered else last_failure,
                "last_call_ok_after_failure": recovered,
                "calls_7d": int(call.get("calls_7d", 0)),
            }
        )

    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "installations": installations,
        "server": {"tool_count": tool_count, "hidden_tool_count": hidden_tool_count},
        "check": _check_summary(installations, tool_count=tool_count),
    }
