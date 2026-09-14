"""方案 §WP7：案件上下文工具与审批绑定。

三件事，每件都对应一条阻断测试
------------------------------
1. **跨租户与越权案件读不到**。而且"读不到"必须与"不存在"不可区分 —— 否则工具
   就成了案件 id 的存在性探测器，而 id 是会被猜的。
2. **列表不返回自由文本**。``description`` 里通常写着员工的具体情况，列表的用途是
   "找到那个案件"，不是"读完它"。
3. **被撤销的安装实例发起的待审批不能再执行**。只挡住新调用的撤销是假的撤销：那条
   已经躺在队列里的写操作在审批通过时照样会落库。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.data.models.base import Base
from app.data.models.oauth import REVOCATION_KIND_FAMILY, OAuthRevokedToken
from app.scenarios.hr_case_agent.service import (
    CasePermissionDeniedError,
    HRCaseService,
)
from app.scenarios.hr_case_agent.state import InvalidTransitionError
from app.shared.errors import NotFoundError


@pytest.fixture()
def session_factory(sqlite_engine):
    return async_sessionmaker(sqlite_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(sqlite_engine):
    from app.data.models import hr_case

    async with sqlite_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    hr_case.HRCase.__table__,
                    hr_case.CasePlan.__table__,
                    hr_case.ApprovalRequest.__table__,
                    hr_case.ToolExecution.__table__,
                    hr_case.CaseEvent.__table__,
                    hr_case.AgentRun.__table__,
                    OAuthRevokedToken.__table__,
                ],
            )
        )


async def _seed_case(session_factory, *, tenant="t1", created_by="u1", title="加班费争议", ready: bool = False) -> str:
    """建一个案件；``ready=True`` 时推进到可提交审批的状态。

    状态机不允许 ``NEW → AWAITING_APPROVAL``，所以提交审批的用例必须先流转。
    这不是测试的麻烦事 —— 它正是"不能凭空给一个刚建的案件提交写操作"这条不变式
    在起作用。
    """
    async with session_factory() as session:
        service = HRCaseService(session, tenant)
        case = await service.create_case(
            created_by=created_by,
            subject_ref="S1",
            category="overtime",
            title=title,
            description="员工张某反映上月加班费未足额发放，附考勤截图。",
        )
        if ready:
            # NEW → TRIAGED → EVIDENCE_READY → PLAN_READY 是状态机允许的唯一到达路径。
            for target in ("TRIAGED", "EVIDENCE_READY", "PLAN_READY"):
                await service.transition_case(case.id, target)
        await session.commit()
        return case.id


# --------------------------------------------------------------------------- #
# 1. 可见性
# --------------------------------------------------------------------------- #


async def test_hrbp_only_lists_cases_they_created(session_factory) -> None:
    mine = await _seed_case(session_factory, created_by="u1", ready=True)
    await _seed_case(session_factory, created_by="u2")

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        cases = await service.search_cases()
    assert [c.id for c in cases] == [mine]


async def test_a_manager_sees_their_visible_scope(session_factory) -> None:
    await _seed_case(session_factory, created_by="u1", ready=True)
    other = await _seed_case(session_factory, created_by="u2")

    async with session_factory() as session:
        # hrbp 与 hr_manager 的差别就是 visible_user_ids：上级能看到下属创建的。
        service = HRCaseService(session, "t1", actor="user:mgr|role:hr_manager", visible_user_ids={"u2"})
        cases = await service.search_cases()
    assert [c.id for c in cases] == [other]


async def test_another_tenants_case_is_not_visible(session_factory) -> None:
    await _seed_case(session_factory, tenant="t2", created_by="u1")

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        assert await service.search_cases() == []


async def test_filters_are_applied(session_factory) -> None:
    overtime = await _seed_case(session_factory, created_by="u1", title="加班费")
    async with session_factory() as session:
        service = HRCaseService(session, "t1")
        await service.create_case(created_by="u1", subject_ref="S2", category="leave", title="年假")
        await session.commit()

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        assert [c.id for c in await service.search_cases(category="overtime")] == [overtime]
        assert await service.search_cases(category="nonexistent") == []


# --------------------------------------------------------------------------- #
# 2. 字段最小化
# --------------------------------------------------------------------------- #


async def test_a_case_you_cannot_see_is_indistinguishable_from_one_that_does_not_exist(
    session_factory,
) -> None:
    """两种情况都走同一条异常 —— 区分它们会让本工具变成 id 的存在性探测器。"""
    hidden = await _seed_case(session_factory, created_by="u2")

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        with pytest.raises(NotFoundError):
            await service.get_case(hidden)
        with pytest.raises(NotFoundError):
            await service.get_case("00000000-0000-0000-0000-000000000000")


async def test_the_case_list_never_carries_free_text(session_factory) -> None:
    """列表的用途是"找到那个案件"，不是"读完它"。"""
    from app.scenarios.hr_case_agent.read_executors import _case_view

    await _seed_case(session_factory, created_by="u1", ready=True)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        case = (await service.search_cases())[0]

    view = _case_view(case)
    assert "description" not in view
    assert "张某" not in str(view)


# --------------------------------------------------------------------------- #
# 3. 审批绑定与撤销
# --------------------------------------------------------------------------- #


async def test_an_approval_records_who_asked_for_it(session_factory) -> None:
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approval = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params={"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
            requester_user_id="u1",
            client_id="workbuddy",
            installation_id="family-1",
        )
        await session.commit()

    assert approval.client_id == "workbuddy"
    assert approval.installation_id == "family-1"
    assert approval.requester_user_id == "u1"


async def test_a_revoked_installation_cannot_get_its_pending_approval_through(session_factory) -> None:
    """这条是本工作包最要紧的一条：撤销必须覆盖**已经提交**的待审批。

    只挡住新调用的撤销是假的 —— 那条写操作还在队列里，审批通过时照样落库。
    """
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approval = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params={"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
            installation_id="family-1",
        )
        session.add(
            OAuthRevokedToken(
                kind=REVOCATION_KIND_FAMILY,
                value="family-1",
                expires_at=approval.expires_at,
                reason="user_revoked",
                tenant_id="t1",
            )
        )
        await session.commit()
        approval_id = approval.id

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:boss|role:hr_manager", visible_user_ids={"u1"})
        with pytest.raises(CasePermissionDeniedError):
            await service.decide_approval(
                case_id, approval_id, approver_id="boss", decision="approve", reason=None, role="hr_manager"
            )


async def test_a_live_installation_can_still_be_decided(session_factory) -> None:
    """上一条不能靠"一律拒绝"通过 —— 没有撤销记录时审批必须能正常走完。"""
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approval = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params={"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
            installation_id="family-live",
        )
        await session.commit()
        approval_id = approval.id

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:boss|role:hr_manager", visible_user_ids={"u1"})
        decided = await service.decide_approval(
            case_id, approval_id, approver_id="boss", decision="approve", reason=None, role="hr_manager"
        )
    assert decided.status == "APPROVED"


async def test_revocation_after_approval_still_blocks_execution(session_factory) -> None:
    """撤销与执行之间的窗口不能让已 APPROVED 的写操作漏过。"""
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    params = {"title": "加班费争议", "subject_ref": "S1", "category": "overtime"}
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approval = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params=params,
            requester_user_id="u1",
            client_id="workbuddy",
            installation_id="family-race",
        )
        await service.decide_approval(
            case_id, approval.id, approver_id="boss", decision="approve", reason=None, role="hr_manager"
        )
        session.add(
            OAuthRevokedToken(
                kind=REVOCATION_KIND_FAMILY,
                value="family-race",
                expires_at=approval.expires_at,
                reason="user_revoked",
                tenant_id="t1",
            )
        )
        await session.commit()
        approval_id = approval.id

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        with pytest.raises(CasePermissionDeniedError, match="revoked"):
            await service.begin_tool_execution(
                case_id,
                "create_hr_case",
                params,
                request_id="req-after-revoke",
                approval_id=approval_id,
            )


async def test_identical_request_from_another_principal_does_not_reuse_approval(session_factory) -> None:
    """参数相同不等于授权主体相同；用户、客户端和安装实例都必须绑定。"""
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    params = {"title": "加班费争议", "subject_ref": "S1", "category": "overtime"}
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        first = await service.request_approval(
            case_id,
            "create_hr_case",
            params,
            requester_user_id="u1",
            client_id="workbuddy",
            installation_id="family-1",
        )
        await session.commit()
        await service.transition_case(case_id, "PLAN_READY")
        second = await service.request_approval(
            case_id,
            "create_hr_case",
            params,
            requester_user_id="u2",
            client_id="workbuddy",
            installation_id="family-2",
        )
        await session.commit()

    assert second.id != first.id
    assert second.requester_user_id == "u2"
    assert second.installation_id == "family-2"


async def test_an_approval_from_another_case_is_not_reachable(session_factory) -> None:
    """按 (case_id, approval_id) 双条件查 —— 单凭 approval_id 就能读到，等于 id 即凭据。"""
    case_a = await _seed_case(session_factory, created_by="u1", ready=True)
    case_b = await _seed_case(session_factory, created_by="u1", title="另一件")
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approval = await service.request_approval(
            case_a,
            tool_name="create_hr_case",
            params={"title": "x", "subject_ref": "S1", "category": "overtime"},
        )
        await session.commit()
        approval_id = approval.id

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        with pytest.raises(NotFoundError):
            await service.get_approval(case_b, approval_id)


async def test_changing_a_parameter_cannot_sneak_past_a_pending_approval(session_factory) -> None:
    """参数哈希绑定 + 每案至多一条待审批，两条一起构成防重放。

    我原本预计"改参数会创建第二条审批"，实际行为更强：**同一个案件同时只能有一条
    待审批**，所以改过参数的提交会被状态机直接拒绝，而不是排到后面。两者都能防重放，
    但后者的含义更清楚 —— 不存在"两条相似的审批"可供混淆，也不存在"审批人点错了
    哪一条"的可能。
    """
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        first = await service.request_approval(
            case_id,
            tool_name="create_hr_case",
            params={"title": "加班费争议", "subject_ref": "S1", "category": "overtime"},
        )
        await session.commit()
        first_hash = first.input_hash

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        # 具体到状态机的拒绝原因：案件已经在等一条审批，不能同时再等另一条。
        with pytest.raises(InvalidTransitionError, match="AWAITING_APPROVAL"):
            await service.request_approval(
                case_id,
                tool_name="create_hr_case",
                params={"title": "加班费争议（改了）", "subject_ref": "S1", "category": "overtime"},
            )
        await session.rollback()

    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        approvals = await service.list_approvals(case_id)
    assert [a.id for a in approvals] == [first.id]
    assert approvals[0].input_hash == first_hash, "原审批的参数哈希不该被后来的提交改写"


async def test_identical_parameters_reuse_the_same_approval(session_factory) -> None:
    """幂等：同样的参数重复提交不该产生第二条审批。"""
    case_id = await _seed_case(session_factory, created_by="u1", ready=True)
    params = {"title": "加班费争议", "subject_ref": "S1", "category": "overtime"}
    async with session_factory() as session:
        service = HRCaseService(session, "t1", actor="user:u1|role:hrbp")
        first = await service.request_approval(case_id, tool_name="create_hr_case", params=params)
        second = await service.request_approval(case_id, tool_name="create_hr_case", params=params)
        await session.commit()
    assert first.id == second.id
