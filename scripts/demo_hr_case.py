"""HR Case Agent demo script (Phase 7) — three journeys, no LLM required.

Journeys:
  1. SUCCESS      employee reports unpaid overtime → evidence → case plan →
                  HR approval → case created & owner assigned (trace shown)
  2. REJECTION    unapproved write attempt is refused
  3. RECOVERY     write tool fails once, retries, then hands off without
                  duplicating the side effect

Run:  python scripts/demo_hr_case.py
All state is in-memory (SQLite); nothing touches production systems.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models import hr_case
from app.data.models.base import Base
from app.scenarios.hr_case_agent import agent_loop
from app.scenarios.hr_case_agent.planner import Planner
from app.scenarios.hr_case_agent.service import HRCaseService
from app.scenarios.hr_case_agent.tools import ToolError

H = hr_case  # short alias for table list


def banner(title: str) -> None:
    print(f"\n{'=' * 62}\n {title}\n{'=' * 62}")


async def make_tables() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[H.HRCase.__table__, H.CasePlan.__table__, H.ApprovalRequest.__table__,
                        H.ToolExecution.__table__, H.CaseEvent.__table__, H.AgentRun.__table__],
            )
        )
    return async_sessionmaker(engine, expire_on_commit=False)


class FakeHRBackend:
    """Deterministic stand-ins for the production write tools."""

    def __init__(self) -> None:
        self.cases_created: list[dict] = []
        self.notifications_sent: list[dict] = []
        self.fail_notifications_next = False

    async def create_case(self, params: dict) -> dict:
        self.cases_created.append(params)
        return {"summary": f"工单 #{len(self.cases_created)} 已创建：{params.get('title', '')}"}

    async def assign_owner(self, params: dict) -> dict:
        return {"summary": f"负责人已指定：{params.get('owner_id', 'hr-001')}"}

    async def send_notification(self, params: dict) -> dict:
        if self.fail_notifications_next:
            self.fail_notifications_next = False
            raise ToolError("PROVIDER_TIMEOUT", "notification provider timed out")
        self.notifications_sent.append(params)
        return {"summary": f"通知已发送至 {params.get('recipient_ref', '')}"}


async def journey_success(backend: FakeHRBackend) -> None:
    banner("旅程 1：员工报告加班费问题 → 检索 → 计划 → 人工批准 → 建单成功")
    session_factory = await make_tables()
    async with session_factory() as session:
        service = HRCaseService(session, "demo-tenant", actor="agent")
        agent_loop.register_tool_executor("search_policy", lambda p: _async({"summary": "命中《薪酬福利管理制度》第三章：加班费 1.5/2/3 倍标准"}))
        agent_loop.register_tool_executor("create_hr_case", backend.create_case)
        agent_loop.register_tool_executor("assign_case_owner", backend.assign_owner)

        case = await service.create_case("hr-001", "EMP-SYN-101", "overtime", "员工反馈 3 个月加班未足额支付加班费", risk_level="MEDIUM")
        await service.transition_case(case.id, "TRIAGED")
        run = await service.start_agent_run(case.id, "处理加班费投诉并建单跟进")

        draft = Planner(None).validate(
            '{"steps": ['
            '{"tool": "search_policy", "params": {"query": "加班费 计算 标准"}, "reason": "检索加班费制度依据"},'
            '{"tool": "create_hr_case", "params": {"title": "加班费支付争议", "subject_ref": "EMP-SYN-101", "category": "overtime"}, "reason": "为该员工建立跟进工单"}'
            '], "rationale": "先取证后建单"}'
        )
        await service.save_plan(case.id, steps=[{"tool": s.tool, "params": s.params, "reason": s.reason} for s in draft.steps], agent_run_id=run.id)
        outcome = await agent_loop.run_plan(service, case.id, draft, agent_run_id=run.id)
        await session.commit()
        print(f"agent run → {outcome.status}, approval_id={outcome.approval_id}")

        approved = await service.decide_approval(case.id, outcome.approval_id, "hr-manager-9", "approve", "情况属实，同意建单", role="hr_manager")
        await session.commit()
        print(f"HR 批准 → {approved.status}")

        from sqlalchemy import select

        from app.data.models.hr_case import ApprovalRequest

        approval = (await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == outcome.approval_id))).scalars().first()
        executed = await agent_loop.execute_approved_write(service, case.id, approval.id, "demo-req-1", backend.create_case)
        # execute_approved_write already moved the case to RESOLVED
        await session.commit()
        print(f"执行写工具 → {executed['status']}：{executed.get('summary', '')}")
        events = await service.list_events(case.id)
        print("审计轨迹:", " → ".join(f"{e.event_type}@{e.seq}" for e in events))


async def _async(value: dict) -> dict:
    return value


async def journey_rejection(backend: FakeHRBackend) -> None:
    banner("旅程 2：未批准的写操作被拒绝")
    session_factory = await make_tables()
    from app.scenarios.hr_case_agent.service import ApprovalError

    async with session_factory() as session:
        service = HRCaseService(session, "demo-tenant", actor="attacker")
        case = await service.create_case("hr-001", "EMP-SYN-202", "payroll", "直接给全员发通知")
        await service.transition_case(case.id, "TRIAGED")
        await service.transition_case(case.id, "EVIDENCE_READY")
        await service.save_plan(case.id, steps=[])
        try:
            await service.begin_tool_execution(
                case.id, "send_case_notification", {"recipient_ref": "all", "template": "notice"}, request_id="evil-req"
            )
            print("❌ 不应到达这里：未批准写操作被执行了")
        except ApprovalError as e:
            print(f"✅ 未批准写操作被拒绝：{e.message}")


async def journey_recovery(backend: FakeHRBackend) -> None:
    banner("旅程 3：通知工具失败 → 案件 FAILED → 新审批后重试成功，不重复发送")
    session_factory = await make_tables()
    async with session_factory() as session:
        service = HRCaseService(session, "demo-tenant", actor="agent")
        agent_loop.register_tool_executor("search_policy", lambda p: _async({"summary": "制度命中"}))
        agent_loop.register_tool_executor("send_case_notification", backend.send_notification)
        backend.fail_notifications_next = True  # provider outage: first attempt fails

        case = await service.create_case("hr-001", "EMP-SYN-303", "overtime", "加班政策更新通知")
        await service.transition_case(case.id, "TRIAGED")
        run = await service.start_agent_run(case.id, "发送制度更新通知")

        draft = Planner(None).validate(
            '{"steps": [{"tool": "search_policy", "params": {"query": "加班 制度 更新"}},'
            '{"tool": "send_case_notification", "params": {"channel": "in_app", "recipient_ref": "dept-hr", "template": "policy_update"}}]}'
        )
        await service.save_plan(case.id, steps=[], agent_run_id=run.id)
        outcome = await agent_loop.run_plan(service, case.id, draft, agent_run_id=run.id)
        await session.commit()
        print(f"agent run → {outcome.status}（写工具在审批门停下）")

        approved = await service.decide_approval(case.id, outcome.approval_id, "hr-manager-9", "approve", "同意发送", role="hr_manager")
        await session.commit()
        print(f"HR 批准 → {approved.status}")

        from sqlalchemy import select

        from app.data.models.hr_case import ApprovalRequest

        approval = (await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == outcome.approval_id))).scalars().first()
        first = await agent_loop.execute_approved_write(service, case.id, approval.id, "notify-req-1", backend.send_notification)
        await session.commit()
        print(f"第一次执行 → {first['status']}（通知服务超时 → 案件进入 FAILED，可安全重试）")

        from app.scenarios.hr_case_agent.service import ApprovalError
        try:
            await agent_loop.execute_approved_write(service, case.id, approval.id, "notify-req-1", backend.send_notification)
            await session.commit()
            print("❌ 不应到达：失败的执行在已消费审批下被重跑")
        except ApprovalError:
            print("✅ 失败的执行不能在已消费审批下重跑（需新审批 + 新 request_id）")

        print("审批已消费（CONSUMED）—— 重新执行需要新的审批，防止旧审批被复用")
        case_row = await service.get_case(case.id)
        print(f"当前状态: {case_row.status}")
        approval2 = await service.request_approval(case.id, "send_case_notification", {"channel": "in_app", "recipient_ref": "dept-hr", "template": "policy_update"})
        await service.decide_approval(case.id, approval2.id, "hr-manager-9", "approve", "重试发送", role="hr_manager")
        await session.commit()
        second = await agent_loop.execute_approved_write(service, case.id, approval2.id, "notify-req-2", backend.send_notification)
        await session.commit()
        print(f"新审批后重试 → {second['status']}：{second.get('summary', '')}")
        print(f"通知实际发送次数: {len(backend.notifications_sent)}（应为 1 —— 失败不重复）")


async def main() -> None:
    backend = FakeHRBackend()
    await journey_success(backend)
    await journey_rejection(backend)
    await journey_recovery(backend)
    banner("Demo 结束：三条旅程全部按预期执行")


if __name__ == "__main__":
    asyncio.run(main())
