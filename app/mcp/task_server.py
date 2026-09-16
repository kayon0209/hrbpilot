"""MCP task facade — the 7+2 task tools of the intent-to-task gateway.

This is NOT a second ruleset. Every tool here:

1. resolves identity exactly like ``app/mcp/server.py`` (``principal_from_headers``);
2. gates on the SAME pure function ``authorize_tool_call`` against the SAME
   merged discovery catalog that ``/mcp`` already uses, so refusing/stripping
   a tool in one place refuses/strips it everywhere;
3. delegates business semantics to ``AgentTaskService``, shared with the REST
   bridge and workbench routes.

Two read-styled task information tools live here by product requirement:

- ``answer_policy_question`` / ``get_case_context`` reuse the atomic read
  implementations (``run_read_tool``) instead of inventing new executors;
  the facade only names them with stable product phrasing and the
  ``response_format`` contract.

Mounting note: ``app.main`` mounts this second ``MCPServer`` at ``/mcp/tasks``,
next to the atomic endpoint at ``/mcp``.  External Agents select which bundle
to configure at connection time (default = task bundle).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.server import MCPServer
from mcp.types import ToolAnnotations

from app.mcp.audit import record_mcp_call
from app.mcp.auth import authorize_tool_call, denial_envelope, log_denial, principal_from_headers
from app.mcp.contract import ToolOutcome, envelope, failure_envelope
from app.mcp.read_dispatch import anonymous_read_envelope
from app.mcp.task_descriptions import TASK_TOOL_DESCRIPTIONS
from app.scenarios.agent_tasks.tools import combined_catalog, validate_task_tool_call
from app.scenarios.hr_case_agent.tools import ToolError
from app.shared.logger import get_logger

logger = get_logger(__name__)

task_mcp_server = MCPServer("hrbpilot-task-gateway")

_SURFACE = "task_gateway"

_READ_TASK_TOOLS = frozenset(
    {
        "get_task_status",
        "list_my_tasks",
        "answer_policy_question",
        "get_case_context",
        "get_my_access_profile",
    }
)

#: atomic read tool reused under a product name (facade-only alias, same executor).
_TASK_READ_ALIAS: dict[str, str] = {
    "answer_policy_question": "search_policy",
    "get_case_context": "get_case_summary",
    "get_my_access_profile": "get_my_access_profile",
}


def _annotations(tool_name: str) -> ToolAnnotations:
    is_read = tool_name in _READ_TASK_TOOLS
    return ToolAnnotations(
        title=tool_name,
        read_only_hint=is_read,
        destructive_hint=tool_name == "submit_hr_action",
        idempotent_hint=True,
        open_world_hint=False,
    )


def _describe(tool_name: str) -> str:
    try:
        return TASK_TOOL_DESCRIPTIONS[tool_name]
    except KeyError as error:  # pragma: no cover
        raise KeyError(f"task tool {tool_name!r} has no TASK_TOOL_DESCRIPTIONS entry") from error


async def _principal_from_ctx(ctx: Context | None):
    if ctx is None:
        return None
    try:
        headers = ctx.headers
    except ValueError:
        return None
    return await principal_from_headers(headers)


def _actor(principal) -> str:
    if principal is None:
        return "mcp:anonymous"
    return f"user:{principal.user_id}|role:{principal.role}"


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _record(
    principal, *, tool: str, outcome: str, started: float, detail: dict | None = None, approval_id: str | None = None
) -> None:
    await record_mcp_call(
        principal,
        tool=tool,
        outcome_code=outcome,
        latency_ms=_elapsed(started),
        approval_id=approval_id,
        detail={**(detail or {}), "surface": _SURFACE},
    )


def _service_session(principal):
    from app.data.database import tenant_session

    return tenant_session(principal.tenant_id)


async def _run_task_write(tool_name: str, validated: dict[str, Any], principal, started: float) -> dict[str, Any]:
    from app.scenarios.agent_tasks.service import AgentTaskService

    async with _service_session(principal) as session:
        service = AgentTaskService(session, principal)
        if tool_name == "prepare_hr_action":
            result = await service.prepare(
                str(validated.get("goal", "")),
                case_ref=validated.get("case_ref"),
                response_format=str(validated.get("response_format", "concise")),
            )
        elif tool_name == "submit_hr_action":
            result = await service.submit(
                str(validated["task_id"]),
                int(validated["draft_version"]),
                str(validated["idempotency_key"]),
            )
        elif tool_name == "provide_task_input":
            result = await service.provide_input(
                str(validated["task_id"]),
                {key: str(value) for key, value in dict(validated.get("fields") or {}).items()},
            )
        elif tool_name == "cancel_task":
            result = await service.cancel(str(validated["task_id"]))
        else:  # pragma: no cover
            return failure_envelope(tool_name, "UNKNOWN_TOOL", tenant_id=principal.tenant_id)
        await _record(
            principal,
            tool=tool_name,
            outcome=str(result.get("outcome") or ""),
            started=started,
            approval_id=result.get("approval_id") if isinstance(result.get("approval_id"), str) else None,
            detail={"object_ref": result.get("task_id")},
        )
        return result


async def _invoke(tool_name: str, params: dict[str, Any], ctx: Context | None) -> dict[str, Any]:
    started = time.perf_counter()
    principal = await _principal_from_ctx(ctx)
    # Gate on the merged discovery catalog: /mcp and /mcp/tasks share one fact.
    decision = authorize_tool_call(principal, tool_name, catalog=combined_catalog())
    if not decision.allowed:
        log_denial(decision, principal, surface=_SURFACE)
        await _record(
            principal,
            tool=tool_name,
            outcome=ToolOutcome.AUTH_REQUIRED.value
            if principal is None
            else (decision.deny_outcome or ToolOutcome.FAILED).value,
            started=started,
        )
        if principal is None:
            return (
                anonymous_read_envelope(tool_name, params)
                if tool_name in _READ_TASK_TOOLS
                else envelope(tool_name, ToolOutcome.AUTH_REQUIRED)
            )
        return denial_envelope(decision, tenant_id=principal.tenant_id)

    assert principal is not None
    try:
        validated = validate_task_tool_call(tool_name, params)
    except ToolError as error:
        return failure_envelope(
            tool_name, error.code, detail=str(error.__cause__) if error.code == "INVALID_PARAMS" else None
        )

    try:
        if tool_name in ("get_task_status", "list_my_tasks"):
            from app.scenarios.agent_tasks.service import AgentTaskService

            async with _service_session(principal) as session:
                service = AgentTaskService(session, principal)
                if tool_name == "get_task_status":
                    result = await service.get_status(
                        str(validated["task_id"]), response_format=str(validated.get("response_format", "concise"))
                    )
                else:
                    result = await service.list_tasks(
                        status=validated.get("status"),
                        limit=int(validated.get("limit", 20)),
                        offset=int(validated.get("offset", 0)),
                        response_format=str(validated.get("response_format", "concise")),
                    )
                await _record(principal, tool=tool_name, outcome=str(result.get("outcome") or ""), started=started)
                return result

        if tool_name in _TASK_READ_ALIAS:
            from app.mcp.read_dispatch import run_read_tool

            alias = _TASK_READ_ALIAS[tool_name]
            read_params = dict(validated)
            if tool_name == "answer_policy_question":
                read_params = {
                    "query": read_params["question"],
                    "top_k": read_params.get("top_k", 3),
                    "detail": "concise",
                }
            elif tool_name == "get_case_context":
                read_params = {"case_id": str(read_params.pop("case_ref")), "detail": "concise"}
            else:
                read_params = {}
            read_params.pop("response_format", None)
            result = await run_read_tool(alias, read_params, principal.tenant_id, principal=principal)
            result = dict(result)
            result["tool"] = tool_name
            await _record(principal, tool=tool_name, outcome=str(result.get("outcome") or ""), started=started)
            return result

        return await _run_task_write(tool_name, validated, principal, started)
    except Exception:
        logger.exception("task_tool_crashed", tool=tool_name, tenant_id=principal.tenant_id)
        return failure_envelope(tool_name, "INTERNAL_ERROR", tenant_id=principal.tenant_id)


@task_mcp_server.tool(
    name="prepare_hr_action",
    description=_describe("prepare_hr_action"),
    annotations=_annotations("prepare_hr_action"),
    structured_output=True,
)
async def prepare_hr_action(
    goal: str,
    case_ref: str | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"goal": goal, "response_format": response_format}
    if case_ref is not None:
        params["case_ref"] = case_ref
    return await _invoke("prepare_hr_action", params, ctx)


@task_mcp_server.tool(
    name="submit_hr_action",
    description=_describe("submit_hr_action"),
    annotations=_annotations("submit_hr_action"),
    structured_output=True,
)
async def submit_hr_action(
    task_id: str, draft_version: int, idempotency_key: str, ctx: Context | None = None
) -> dict[str, Any]:
    return await _invoke(
        "submit_hr_action",
        {"task_id": task_id, "draft_version": draft_version, "idempotency_key": idempotency_key},
        ctx,
    )


@task_mcp_server.tool(
    name="provide_task_input",
    description=_describe("provide_task_input"),
    annotations=_annotations("provide_task_input"),
    structured_output=True,
)
async def provide_task_input(
    task_id: str, fields: dict[str, str] | None = None, ctx: Context | None = None
) -> dict[str, Any]:
    return await _invoke("provide_task_input", {"task_id": task_id, "fields": fields or {}}, ctx)


@task_mcp_server.tool(
    name="get_task_status",
    description=_describe("get_task_status"),
    annotations=_annotations("get_task_status"),
    structured_output=True,
)
async def get_task_status(
    task_id: str, response_format: Literal["concise", "detailed"] = "concise", ctx: Context | None = None
) -> dict[str, Any]:
    return await _invoke("get_task_status", {"task_id": task_id, "response_format": response_format}, ctx)


@task_mcp_server.tool(
    name="list_my_tasks",
    description=_describe("list_my_tasks"),
    annotations=_annotations("list_my_tasks"),
    structured_output=True,
)
async def list_my_tasks(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    response_format: Literal["concise", "detailed"] = "concise",
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset, "response_format": response_format}
    if status is not None:
        params["status"] = status
    return await _invoke("list_my_tasks", params, ctx)


@task_mcp_server.tool(
    name="cancel_task",
    description=_describe("cancel_task"),
    annotations=_annotations("cancel_task"),
    structured_output=True,
)
async def cancel_task(task_id: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _invoke("cancel_task", {"task_id": task_id}, ctx)


@task_mcp_server.tool(
    name="answer_policy_question",
    description=_describe("answer_policy_question"),
    annotations=_annotations("answer_policy_question"),
    structured_output=True,
)
async def answer_policy_question(
    question: str,
    top_k: int = 3,
    response_format: Literal["concise", "detailed"] = "concise",
    ctx: Context | None = None,
) -> dict[str, Any]:
    return await _invoke(
        "answer_policy_question", {"question": question, "top_k": top_k, "response_format": response_format}, ctx
    )


@task_mcp_server.tool(
    name="get_case_context",
    description=_describe("get_case_context"),
    annotations=_annotations("get_case_context"),
    structured_output=True,
)
async def get_case_context(
    case_ref: str, response_format: Literal["concise", "detailed"] = "concise", ctx: Context | None = None
) -> dict[str, Any]:
    return await _invoke("get_case_context", {"case_ref": case_ref, "response_format": response_format}, ctx)


@task_mcp_server.tool(
    name="get_my_access_profile",
    description=_describe("get_my_access_profile"),
    annotations=_annotations("get_my_access_profile"),
    structured_output=True,
)
async def get_my_access_profile(ctx: Context | None = None) -> dict[str, Any]:
    return await _invoke("get_my_access_profile", {}, ctx)
