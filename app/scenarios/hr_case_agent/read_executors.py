"""Production read-tool executors for the HR Case agent loop.

Why this module exists
----------------------
``agent_loop.run_plan`` executes READ plan steps through the process-global
``TOOL_EXECUTORS`` registry, but nothing registered a production executor, so
every read step ended as ``HANDED_OFF`` with ``no executor registered for
search_policy``.  That silently downgraded every case to a human — the agent
could validate plans it was never able to execute.

These executors reuse the same real hybrid-retrieval service as the MCP read
tools (``app.rag.retrieval.retriever.Retriever``): dense Milvus + sparse
PostgreSQL FTS + RRF fusion, tenant-scoped.

There is deliberately **no fabricated fallback**: if retrieval or the datastore
is unreachable the executor raises ``ToolError``, so the loop retries once and
then hands off to a human instead of inventing a policy answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.data.database import make_tenant_session
from app.data.models.hr_case import ApprovalRequest, HRCase
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.scenarios.hr_case_agent.agent_loop import register_tool_executor
from app.scenarios.hr_case_agent.read_context import current_read_principal, current_read_tenant
from app.scenarios.hr_case_agent.tools import ToolError
from app.shared.logger import get_logger

if TYPE_CHECKING:  # 只在类型标注里用到：顶层导入会与 app.mcp.* 形成循环
    from app.mcp.auth.principal import McpPrincipal

logger = get_logger(__name__)

# Retrieval is scoped to one knowledge base; the policy QA base is the default
# target for case evidence gathering.
DEFAULT_KB_SCENARIO = "policy_qa"


def _require_tenant() -> str:
    tenant_id = current_read_tenant()
    if not tenant_id:
        raise ToolError(
            "TENANT_CONTEXT_MISSING",
            "read executor was invoked outside a bound tenant context (run_plan binds it)",
        )
    return tenant_id


async def _resolve_kb_id(tenant_id: str, kb_id: str | None) -> str:
    """Use the requested KB, else the tenant's oldest active policy KB."""
    if kb_id:
        return str(kb_id)
    session = await make_tenant_session(tenant_id)
    try:
        row = await session.scalar(
            select(KnowledgeBase.id)
            .where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.status == "active")
            .order_by(KnowledgeBase.created_at.asc())
            .limit(1)
        )
    finally:
        await session.close()
    if not row:
        raise ToolError("NO_KNOWLEDGE_BASE", "tenant has no active knowledge base to search")
    return str(row)


def _summary_from_chunks(chunks: list[dict], kb_id: str) -> str:
    head = chunks[0]
    section = head.get("section") or "未标注章节"
    source = head.get("source") or "未知文档"
    preview = str(head.get("content", "")).strip().replace("\n", " ")[:180]
    return f"命中 {len(chunks)} 条制度片段（kb={kb_id}）：{source} / {section} — {preview}"


async def execute_search_policy(params: dict) -> dict:
    """Hybrid RAG search over the tenant's policy knowledge base."""
    tenant_id = _require_tenant()
    kb_id = await _resolve_kb_id(tenant_id, params.get("kb_id"))
    top_k = int(params.get("top_k") or 3)

    from app.rag.retrieval.retriever import Retriever

    try:
        chunks = await Retriever().retrieve(
            query=str(params["query"]),
            kb_id=kb_id,
            top_k=top_k,
            tenant_id=tenant_id,
        )
    except Exception as e:  # infrastructure failure → retry then human handoff
        logger.warning("read_tool_retrieval_failed", tool="search_policy", kb_id=kb_id, error=repr(e))
        raise ToolError("RETRIEVAL_UNAVAILABLE", f"{type(e).__name__}: {e}") from e

    if not chunks:
        return {
            "summary": f"未命中制度片段（kb={kb_id}）：检索通道可用但无匹配，应按无依据处理",
            "chunks": [],
            "kb_id": kb_id,
        }
    return {
        "summary": _summary_from_chunks(chunks, kb_id),
        "chunks": chunks[:top_k],
        "kb_id": kb_id,
    }


async def execute_get_policy_source(params: dict) -> dict:
    """Hydrate one policy document (optionally one section) for citation."""
    tenant_id = _require_tenant()
    document_name = str(params["document_name"])
    section = params.get("section")

    session = await make_tenant_session(tenant_id)
    try:
        document = (
            await session.execute(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.filename.ilike(f"%{document_name}%"),
                )
                .order_by(Document.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if document is None:
            return {
                "summary": f"未找到文档《{document_name}》：检索不到即不应引用，请人工确认制度来源",
                "document": None,
            }

        chunk_stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == document.id,
            )
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(5)
        )
        if section:
            chunk_stmt = chunk_stmt.where(DocumentChunk.section.ilike(f"%{section}%"))
        chunks = list((await session.execute(chunk_stmt)).scalars().all())
    finally:
        await session.close()

    preview = " ".join((c.content or "").strip().replace("\n", " ") for c in chunks)[:200]
    located = f"/{section}" if section else ""
    return {
        "summary": f"已定位《{document.filename}》{located}，共 {len(chunks)} 段：{preview}",
        "document": {
            "document_id": document.id,
            "filename": document.filename,
            "status": document.status,
            "section": section or "",
            "chunks": [
                {"chunk_index": c.chunk_index, "section": c.section, "content": c.content[:600]} for c in chunks
            ],
        },
    }


async def execute_get_my_access_profile(params: dict) -> dict:
    """当前凭据的身份摘要与可用范围。

    它不需要查库 —— 数据全在**已绑定的主体**里。但仍然走读执行器这条路，理由与
    其它读工具一样：新增出口分支正是同一个工具长出两套实现的方式。

    ``params`` 只用于满足签名；真正的输入是绑定在上下文里的主体，**不接受**由调用
    方传入身份（那会把"你是谁"重新变成可以伪造的参数）。

    legacy agent loop 也会注册这个执行器，但那条路径不绑定主体 —— 那时这里会抛
    ``ToolError``，这是正确的：循环里的读步骤没有"外部 Agent 凭据"这个概念。
    """
    from app.mcp.profile import access_profile
    from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

    _ = params
    principal = current_read_principal()
    if principal is None:
        raise ToolError(
            "PRINCIPAL_CONTEXT_MISSING",
            "access profile requires a bound principal (MCP transports bind it)",
        )
    return access_profile(principal, TOOL_CATALOG)  # type: ignore[arg-type]


def _require_principal() -> McpPrincipal:
    """取当前已绑定主体。缺失即失败 —— 案件相关工具没有"匿名降级"这一档。"""

    principal = current_read_principal()
    if not isinstance(principal, McpPrincipal):
        raise ToolError(
            "PRINCIPAL_CONTEXT_MISSING",
            "case tools require a verified principal (MCP transports bind it)",
        )
    return principal


def _case_view(case: HRCase) -> dict[str, Any]:
    """案件的最小字段集。

    **不含** ``description``：那是自由文本，通常写着员工的具体情况。列表的用途是
    "找到那个案件"，不是"读完它"。也不含任何真实员工标识 —— ``subject_ref`` 是
    合成引用，本来就是为这个用途设计的。
    """
    return {
        "case_id": case.id,
        "title": case.title,
        "subject_ref": case.subject_ref,
        "category": case.category,
        "risk_level": case.risk_level,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


def _approval_view(approval: ApprovalRequest) -> dict[str, Any]:
    """审批的最小字段集。**不含** ``params_json`` —— 那是工具参数的原文。"""
    return {
        "approval_id": approval.id,
        "case_id": approval.case_id,
        "tool_name": approval.tool_name,
        "status": approval.status,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }


async def execute_search_cases(params: dict) -> dict:
    """按 ACL 搜索案件。跨租户与越权案件在这里就不可见 —— 不是"返回空摘要"。"""
    from app.scenarios.hr_case_agent.service import HRCaseService

    tenant_id = _require_tenant()
    principal = _require_principal()

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal))
        cases = await service.search_cases(
            limit=int(params.get("limit") or 20),
            status=params.get("status"),
            category=params.get("category"),
        )
    finally:
        await session.close()

    if not cases:
        return {"summary": "没有找到你有权查看的案件。", "cases": [], "count": 0}
    return {
        "summary": f"找到 {len(cases)} 个案件。",
        "cases": [_case_view(case) for case in cases],
        "count": len(cases),
    }


async def execute_get_case_summary(params: dict) -> dict:
    """读取单个有权案件的上下文。看不见的案件按"不存在"处理（不泄漏存在性）。"""
    from app.scenarios.hr_case_agent.service import HRCaseService
    from app.shared.errors import NotFoundError

    tenant_id = _require_tenant()
    principal = _require_principal()
    case_id = str(params["case_id"])

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal))
        try:
            case = await service.get_case(case_id)
        except NotFoundError:
            # 与"案件真的不存在"返回同一个结果：区分两者会让本工具变成案件 id
            # 的存在性探测器，而 id 是会被猜的。
            return {"summary": "没有找到这个案件，或你无权查看它。", "case": None}
        view = _case_view(case)
        view["description"] = case.description
        approvals = await service.list_approvals(case_id)
    finally:
        await session.close()

    view["approvals"] = [_approval_view(item) for item in approvals]
    return {"summary": f"案件 {case.id} 当前状态 {case.status}。", "case": view}


async def execute_get_approval_status(params: dict) -> dict:
    """查询审批状态。只能查调用者有权查看的案件下的审批。"""
    from app.scenarios.hr_case_agent.service import HRCaseService
    from app.shared.errors import NotFoundError

    tenant_id = _require_tenant()
    principal = _require_principal()
    case_id = str(params["case_id"])

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal))
        try:
            approval_id = params.get("approval_id")
            if approval_id:
                approvals = [await service.get_approval(case_id, str(approval_id))]
            else:
                approvals = await service.list_approvals(case_id)
        except NotFoundError:
            return {"summary": "没有找到这个审批，或你无权查看它。", "approvals": []}
    finally:
        await session.close()

    return {
        "summary": f"共 {len(approvals)} 条审批记录。",
        "approvals": [_approval_view(item) for item in approvals],
    }


def _actor_label(principal: McpPrincipal) -> str:
    """与 ``app/mcp/server.py`` 相同的 actor 写法，供 HRCaseService 解析出 id/role。"""
    return f"user:{principal.user_id}|role:{principal.role}"


READ_TOOL_EXECUTORS = {
    "search_policy": execute_search_policy,
    "get_policy_source": execute_get_policy_source,
    "get_my_access_profile": execute_get_my_access_profile,
    "search_cases": execute_search_cases,
    "get_case_summary": execute_get_case_summary,
    "get_approval_status": execute_get_approval_status,
}


def register_read_tool_executors() -> list[str]:
    """Register production read executors; returns the registered tool names."""
    for tool_name, executor in READ_TOOL_EXECUTORS.items():
        register_tool_executor(tool_name, executor)
    logger.info("hr_case_read_executors_registered", tools=sorted(READ_TOOL_EXECUTORS))
    return sorted(READ_TOOL_EXECUTORS)
