"""Agent task gateway tests — state machine, intent compiler, and manifest drift."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.data.models.agent_task import AgentTask
from app.main import create_app
from app.mcp.auth.principal import AuthMethod, McpPrincipal
from app.mcp.capabilities import assert_manifest_is_current, build_manifest
from app.mcp.task_descriptions import TASK_TOOL_DESCRIPTIONS
from app.mcp.task_server import task_mcp_server
from app.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from app.scenarios.agent_tasks import state as task_state
from app.scenarios.agent_tasks.intent import FIELD_ASK_ORDER, compile_goal, detect_task_type, parse_relative_date
from app.scenarios.agent_tasks.service import AgentTaskService
from app.scenarios.agent_tasks.tools import AGENT_TASK_CATALOG, combined_catalog, validate_task_tool_call
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, ToolError


def test_state_machine_terminal_states_are_closed() -> None:
    for terminal in ("succeeded", "failed", "cancelled", "expired"):
        assert task_state.can_transition(terminal, "draft") is False
        assert task_state.can_transition(terminal, "ready_for_confirmation") is False


def test_state_machine_draft_cycle() -> None:
    assert task_state.transition("draft", "input_required") == "input_required"
    assert task_state.transition("input_required", "ready_for_confirmation") == "ready_for_confirmation"
    assert task_state.transition("ready_for_confirmation", "awaiting_approval") == "awaiting_approval"


def test_stale_version_must_not_be_submittable() -> None:
    # 版本不一致是提交层错误码 TASK_STALE_VERSION 的前置语义，这里只钉状态机：
    # ready→ready 自环允许“又补了一轮”，但 ready→draft（回退）不允许。
    assert task_state.can_transition("ready_for_confirmation", "ready_for_confirmation") is True
    assert task_state.can_transition("ready_for_confirmation", "draft") is False


def test_cancel_is_blocked_after_submit() -> None:
    assert task_state.can_transition("awaiting_approval", "cancelled") is False
    assert task_state.can_transition("queued", "cancelled") is False
    assert task_state.can_transition("draft", "cancelled") is True


def test_intent_detects_probation_type() -> None:
    assert detect_task_type("帮张三建一个试用期跟进任务，下周三前由李经理处理") == "probation_followup"


def test_intent_followup_needs_case_ref_first() -> None:
    result = compile_goal("给一个案件建跟进任务")
    assert result.task_type == "case_followup"
    assert [missing["field"] for missing in result.missing] == ["case_ref", "title"]


def test_intent_ask_order_is_stable() -> None:
    assert list(FIELD_ASK_ORDER[:2]) == ["case_ref", "title"]


def test_intent_parses_next_wednesday_deterministically() -> None:
    monday = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    iso = parse_relative_date("下周三前由李经理处理", monday)
    assert iso is not None
    parsed = datetime.fromisoformat(iso)
    assert (parsed.weekday(), parsed.date().isoformat()) == (2, "2026-09-23")
    assert parse_relative_date("尽快处理", monday) is None


def test_intent_never_invents_owner_id() -> None:
    result = compile_goal("把案件转给李经理", case_ref="case-1")
    assert result.params.get("owner") == "李经理"
    assert "_owner_user_id" not in result.params


def test_catalog_bundles_only_share_connection_profile() -> None:
    atomic = {tool.name for tool in TOOL_CATALOG.tools}
    task = {tool.name for tool in AGENT_TASK_CATALOG.tools}
    assert atomic & task == {"get_my_access_profile"}
    merged = {tool.name for tool in combined_catalog().tools}
    assert merged == atomic | task


def test_task_projection_keeps_the_legacy_governed_work_task_reference() -> None:
    task = SimpleNamespace(work_task_id=None)
    execution = SimpleNamespace(
        tool_name="create_work_task",
        result_summary="work task 11111111-1111-4111-8111-111111111111 created",
    )

    AgentTaskService._project_execution_refs(None, task, execution)  # type: ignore[arg-type]

    assert task.work_task_id == "11111111-1111-4111-8111-111111111111"


def test_task_catalog_uses_stable_scopes() -> None:
    scopes = {tool.required_scope.value for tool in AGENT_TASK_CATALOG.tools}
    assert scopes <= {"hrb:case:read", "hrb:case:propose", "hrb:policy:read", "hrb:profile:read"}


def test_facade_registers_every_task_tool_with_annotations() -> None:
    tools = {tool.name: tool for tool in task_mcp_server._tool_manager.list_tools()}
    assert set(tools) == {tool.name for tool in AGENT_TASK_CATALOG.tools}
    assert tools["submit_hr_action"].annotations is not None
    assert tools["submit_hr_action"].annotations.destructive_hint is True
    assert all(tool.annotations is not None and tool.annotations.open_world_hint is False for tool in tools.values())


def test_submit_rejects_business_params() -> None:
    validated = validate_task_tool_call(
        "submit_hr_action", {"task_id": "t-1", "draft_version": 1, "idempotency_key": "k-12345678"}
    )
    assert sorted(validated) == ["draft_version", "idempotency_key", "task_id"]
    try:
        validate_task_tool_call(
            "submit_hr_action", {"task_id": "t-1", "draft_version": 1, "idempotency_key": "k-12345678", "title": "x"}
        )
    except ToolError as error:
        assert error.code == "INVALID_PARAMS"
    else:  # pragma: no cover
        raise AssertionError("submit must not accept business params")


def test_descriptions_cover_both_bundles() -> None:
    assert set(TOOL_DESCRIPTIONS) == {tool.name for tool in TOOL_CATALOG.tools}
    assert set(TASK_TOOL_DESCRIPTIONS) == {tool.name for tool in AGENT_TASK_CATALOG.tools}
    for name, description in TASK_TOOL_DESCRIPTIONS.items():
        assert "何时用" in description and "何时不用" in description and "【例子】" in description, name


def test_manifest_is_current_and_bundles_are_stable() -> None:
    assert_manifest_is_current()
    manifest = build_manifest()
    assert manifest["bundles"]["task"]["entrypoint"] == "/mcp/tasks"
    assert "prepare_hr_action" in manifest["bundles"]["task"]["tools"]
    assert "submit_hr_action" in manifest["bundles"]["task"]["tools"]
    assert (
        manifest["scope_packages"]["query_and_propose"]["scopes"] != manifest["scope_packages"]["query_only"]["scopes"]
    )


def test_task_gateway_is_matched_before_atomic_mcp_mount() -> None:
    """`/mcp` is a prefix mount, so the more specific task endpoint must win."""
    with TestClient(create_app()) as client:
        response = client.post(
            "/mcp/tasks",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test"}},
            },
        )
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


# --------------------------------------------------------------------------- #
# AgentTaskService —— 服务层行为守护
#
# 针对的是一处真实故障：`_mark_expired` 原先没有终态短路，对已经 `expired` 的
# 任务再走一次会撞 `transition("expired", "expired")`（EXPIRED 是终态、
# TRANSITIONS 为空集），抛 InvalidAgentTaskTransitionError。后果不是 500，而是
# **错误的语义 + 会诱导重试的文案**：MCP 侧被 _invoke 兜成 INTERNAL_ERROR
# （"请稍后重试"），外部 AI Agent 会因此无限重试且必然失败。
#
# 可达性前提（决定短路该覆盖哪些状态）：submit() 先拦掉 _POST_SUBMIT，
# _guard_editable 先拦掉非 DRAFTISH，因此真正能走到 `_mark_expired` 的只有
# 「终态」与「草稿期三态」两组。
# --------------------------------------------------------------------------- #


def _service_without_db() -> AgentTaskService:
    """不需要数据库的服务实例：只用于调用不碰 session 的方法。"""
    principal = McpPrincipal(
        tenant_id="tenant-unit",
        user_id="user-unit",
        role="hrbp",
        auth_method=AuthMethod.INTERNAL,
        client_id="pytest",
    )
    return AgentTaskService(session=None, principal=principal)  # type: ignore[arg-type]


def _transient_task(status: str) -> AgentTask:
    """构造一个未入库的 AgentTask —— 不 add、不 flush、不碰数据库。"""
    return AgentTask(
        id="task-unit",
        tenant_id="tenant-unit",
        requester_user_id="user-unit",
        source_surface="web",
        task_type="case_followup",
        goal_summary="单测构造",
        canonical_params_json="{}",
        input_hash="0" * 64,
        draft_version=1,
        status=status,
    )


def test_mark_expired_is_idempotent_on_every_terminal_state() -> None:
    """终态再标记一次必须安静返回，不得抛错 —— 这正是线上那条故障。"""
    service = _service_without_db()
    for terminal in sorted(task_state.TERMINAL_STATUSES):
        task = _transient_task(terminal)
        service._mark_expired(task)  # 不得抛 InvalidAgentTaskTransitionError
        assert task.status == terminal, terminal


def test_mark_expired_still_marks_draftish_states() -> None:
    """短路不能顺手把正常路径也短路掉：草稿期仍要真的变成 expired。"""
    service = _service_without_db()
    for draftish in (task_state.DRAFT, task_state.INPUT_REQUIRED, task_state.READY_FOR_CONFIRMATION):
        task = _transient_task(draftish)
        service._mark_expired(task)
        assert task.status == task_state.EXPIRED, draftish


def test_intent_refuses_to_freeze_a_generic_sentence_as_title() -> None:
    """泛化说法不得冻结成标题：宁可多问一句，也不把整句当标题。"""
    result = compile_goal("给一个案件建跟进任务")
    assert "title" not in result.params
    assert "title" in [missing["field"] for missing in result.missing]


def test_intent_only_parses_high_confidence_dates() -> None:
    """日期只认高置信写法；歧义写法返回 None（宁可问，不猜）。"""
    now = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    assert parse_relative_date("2026-09-23", now) == "2026-09-23T09:00:00+00:00"
    assert parse_relative_date("尽快处理", now) is None
    assert parse_relative_date("过几天再说", now) is None
