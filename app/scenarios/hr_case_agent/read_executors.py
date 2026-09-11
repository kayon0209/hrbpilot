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

from sqlalchemy import select

from app.data.database import make_tenant_session
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.scenarios.hr_case_agent.agent_loop import register_tool_executor
from app.scenarios.hr_case_agent.read_context import current_read_tenant
from app.scenarios.hr_case_agent.tools import ToolError
from app.shared.logger import get_logger

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
                {"chunk_index": c.chunk_index, "section": c.section, "content": c.content[:600]}
                for c in chunks
            ],
        },
    }


READ_TOOL_EXECUTORS = {
    "search_policy": execute_search_policy,
    "get_policy_source": execute_get_policy_source,
}


def register_read_tool_executors() -> list[str]:
    """Register production read executors; returns the registered tool names."""
    for tool_name, executor in READ_TOOL_EXECUTORS.items():
        register_tool_executor(tool_name, executor)
    logger.info("hr_case_read_executors_registered", tools=sorted(READ_TOOL_EXECUTORS))
    return sorted(READ_TOOL_EXECUTORS)
