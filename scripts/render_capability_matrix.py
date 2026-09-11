"""从代码渲染「角色 × 能力」矩阵（2026-09-10）。

直接读 `app.access.middleware.rbac` 的 `ROLE_CAPABILITIES` 与
`ROUTE_CAPABILITY_MAP`，保证文档与实现永远同步 —— 改了代码不重跑就会露馅，
这才是交付文档该有的形态。

用法::

    python scripts/render_capability_matrix.py            # 写到 docs/operations/role-capability-matrix.md
    python scripts/render_capability_matrix.py --stdout   # 只打印
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.access.middleware.rbac import (  # noqa: E402
    ROLE_CAPABILITIES,
    ROUTE_CAPABILITY_MAP,
)

ROLE_LABEL = {
    "employee": "员工",
    "hrbp": "HRBP",
    "hr_manager": "HR 经理",
    "admin": "平台管理员",
}

CAPABILITY_LABEL = {
    "policy_qa": "制度问答",
    "employee_request": "我的请求",
    "notifications": "通知",
    "interview_digest": "面试纪要",
    "voice_insight": "声音洞察",
    "weekly_report": "周报",
    "culture_content": "文化内容",
    "hr_case": "HR 案例",
    "hr_request_triage": "请求分诊",
    "work_summary": "工作总结",
    "knowledge_feedback": "知识反馈",
    "kb_management": "知识库管理",
    "evaluation": "评测",
    "settings": "系统设置",
    "audit_read": "审计查询",
    "data_source_admin": "数据源管理",
    "user_admin": "用户管理",
}

DEFAULT_ROLE_ORDER = ["employee", "hrbp", "hr_manager", "admin"]


def _roles() -> list[str]:
    ordered = [r for r in DEFAULT_ROLE_ORDER if r in ROLE_CAPABILITIES]
    ordered += [r for r in ROLE_CAPABILITIES if r not in ordered]
    return ordered


def _capabilities() -> list[str]:
    caps: list[str] = []
    for role in _roles():
        for cap in ROLE_CAPABILITIES[role]:
            if cap not in caps:
                caps.append(cap)
    for cap in ROUTE_CAPABILITY_MAP.values():
        if cap not in caps:
            caps.append(cap)
    return sorted(caps)


def render() -> str:
    roles = _roles()
    caps = _capabilities()

    lines = [
        "# HRBPilot 角色 × 能力矩阵",
        "",
        "> 由 `scripts/render_capability_matrix.py` 从 `app/access/middleware/rbac.py` 直接渲染。",
        "> 改了 RBAC 不重跑本脚本，文档就会与实现不一致 —— 请勿手改本表。",
        "",
        "## 1. 角色 × 能力",
        "",
        "| 能力 | 说明 | " + " | ".join(ROLE_LABEL.get(r, r) for r in roles) + " |",
        "| --- | --- | " + " | ".join(":-:" for _ in roles) + " |",
    ]
    for cap in caps:
        label = CAPABILITY_LABEL.get(cap, cap)
        marks = ["✅" if cap in ROLE_CAPABILITIES.get(r, set()) else "—" for r in roles]
        lines.append(f"| `{cap}` | {label} | " + " | ".join(marks) + " |")

    lines += [
        "",
        "## 2. 路由 → 所需能力 → 可访问角色",
        "",
        "| 路由前缀 | 所需能力 | 可访问角色 |",
        "| --- | --- | --- |",
    ]
    for route in sorted(ROUTE_CAPABILITY_MAP, key=len):
        cap = ROUTE_CAPABILITY_MAP[route]
        allowed = [ROLE_LABEL.get(r, r) for r in roles if cap in ROLE_CAPABILITIES.get(r, set())]
        lines.append(f"| `{route}` | `{cap}` | " + ("、".join(allowed) if allowed else "**无（需显式授权）**") + " |")

    lines += [
        "",
        "## 3. 设计说明（面试可讲的三条）",
        "",
        "1. **角色之间不继承**：能力是集合，不是等级。平台管理员 `admin` **默认拿不到任何 HR 业务内容**"
        "（面试/声音/周报/案例都不行）——需要业务能力必须显式持有业务角色，避免「管理员看到全公司员工数据」。",
        "2. **三层鉴权**：路由前缀由 `RBACMiddleware` 粗粒度拦截 → 对象级 ACL 在服务层（`access/object_scope.py`）→ "
        "`tenant_id` 只作隔离边界、不参与授权判定。",
        "3. **不在路由表里的路径没有中间件保护**：例如 `/api/material-batches` 依赖 handler 内的能力门，"
        "这是唯一一道门 —— 新增路由时必须显式登记。",
        "",
        "---",
        "",
        "*自动生成，勿手改。*",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true", help="只打印到标准输出")
    args = parser.parse_args()

    md = render()
    if args.stdout:
        print(md)
        return 0
    out = ROOT / "docs" / "operations" / "role-capability-matrix.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
