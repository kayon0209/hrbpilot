"""``AgentTaskService.approval_queue`` 的行为测试。

覆盖三件事：
① 角色门 —— 只有 ``DECIDER_ROLES``（hr_manager）能看队列，其它角色 403；
② 可见性 —— 队列返回的是**别人**发起、停在 ``awaiting_approval`` 的任务，
   且带决定所需的两条 ID（``case_id`` / ``approval_id``）与提交人姓名；
   这正是 ``list_tasks``（按发起人过滤）永远给不了的那个视图。
③ 过滤精度 —— 非等待态、缺 ``approval_id``/``case_id``、别租户的任务不出现。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.data.models.agent_task import AgentTask
from app.data.models.base import Base
from app.data.models.user import User
from app.mcp.auth.principal import AuthMethod, McpPrincipal
from app.scenarios.agent_tasks.service import AgentTaskService
from app.shared.errors import AppError

TENANT = "tenant-a"


@pytest.fixture()
def session_factory(sqlite_engine):
    return async_sessionmaker(sqlite_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(sqlite_engine):
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[User.__table__, AgentTask.__table__],
            )
        )


def _principal(role: str, user_id: str = "manager-1") -> McpPrincipal:
    return McpPrincipal(
        tenant_id=TENANT,
        user_id=user_id,
        role=role,
        auth_method=AuthMethod.INTERNAL,
        client_id="pytest",
    )


async def _seed_user(session_factory, user_id: str, name: str, tenant: str = TENANT) -> None:
    async with session_factory() as session:
        session.add(
            User(
                id=user_id,
                tenant_id=tenant,
                name=name,
                email=f"{user_id}@example.test",
                hashed_password="x",
                role="hrbp",
            )
        )
        await session.commit()


async def _seed_task(
    session_factory,
    task_id: str,
    *,
    status: str = "awaiting_approval",
    approval_id: str | None = "ap-1",
    case_id: str | None = "case-1",
    requester: str = "hrbp-1",
    tenant: str = TENANT,
) -> None:
    async with session_factory() as session:
        session.add(
            AgentTask(
                id=task_id,
                tenant_id=tenant,
                requester_user_id=requester,
                source_surface="mcp",
                task_type="case_followup",
                goal_summary="帮张三建试用期跟进任务",
                canonical_params_json="{}",
                input_hash="0" * 64,
                draft_version=1,
                status=status,
                approval_id=approval_id,
                case_id=case_id,
            )
        )
        await session.commit()


async def test_non_decider_roles_are_rejected(session_factory) -> None:
    service = AgentTaskService(session_factory(), _principal("hrbp"))
    with pytest.raises(AppError) as exc:
        await service.approval_queue()
    assert exc.value.status_code == 403
    assert exc.value.code == "APPROVAL_DECIDER_REQUIRED"


async def test_manager_sees_other_requesters_awaiting_tasks(session_factory) -> None:
    await _seed_user(session_factory, "hrbp-1", "王 HR")
    await _seed_task(session_factory, "task-1")
    service = AgentTaskService(session_factory(), _principal("hr_manager"))
    report = await service.approval_queue()
    assert report["items"], "hr_manager 必须能看到别人发起的待审批任务"
    item = report["items"][0]
    assert item["task_id"] == "task-1"
    # 决定所需的两组字段缺一不可：批准走 hr-cases 接口，两条 ID 都要送到。
    assert item["case_id"] == "case-1"
    assert item["approval_id"] == "ap-1"
    assert item["requester_name"] == "王 HR"


async def test_queue_excludes_wrong_state_missing_ids_and_other_tenant(session_factory) -> None:
    await _seed_user(session_factory, "hrbp-1", "王 HR")
    await _seed_task(session_factory, "task-running", status="running")
    await _seed_task(session_factory, "task-no-approval", approval_id=None)
    await _seed_task(session_factory, "task-no-case", case_id=None)
    await _seed_task(session_factory, "task-other-tenant", tenant="tenant-b")
    service = AgentTaskService(session_factory(), _principal("hr_manager"))
    report = await service.approval_queue()
    ids = [item["task_id"] for item in report["items"]]
    assert ids == [], f"以上四种都不应出现在队列里，实际: {ids}"
