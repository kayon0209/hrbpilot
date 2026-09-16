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

from sqlalchemy import func, select

from app.data.database import make_tenant_session
from app.data.models.hr_case import ApprovalRequest, HRCase
from app.data.models.knowledge_base import (
    AUTHORITATIVE_AUTHORITIES,
    Document,
    DocumentChunk,
    KnowledgeBase,
)
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
    """Use the requested KB, else prefer the active policy KB with evidence.

    A tenant can retain an old sandbox KB beside a later, curated policy KB.
    Choosing by creation time alone makes the sandbox silently win even when it
    contains only unknown/template sources.  Prefer the base with the most
    declared national-law or company-policy documents; creation time remains a
    deterministic tie-breaker for equally curated bases.
    """
    if kb_id:
        return str(kb_id)
    session = await make_tenant_session(tenant_id)
    try:
        authoritative_documents = (
            select(func.count(Document.id))
            .where(
                Document.tenant_id == tenant_id,
                Document.kb_id == KnowledgeBase.id,
                Document.authority.in_(AUTHORITATIVE_AUTHORITIES),
            )
            .correlate(KnowledgeBase)
            .scalar_subquery()
        )
        row = await session.scalar(
            select(KnowledgeBase.id)
            .where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.scenario_id == DEFAULT_KB_SCENARIO,
                KnowledgeBase.status == "active",
            )
            .order_by(authoritative_documents.desc(), KnowledgeBase.created_at.asc())
            .limit(1)
        )
    finally:
        await session.close()
    if not row:
        raise ToolError("NO_KNOWLEDGE_BASE", "tenant has no active knowledge base to search")
    return str(row)


def _degradation_payload(diags: Any) -> dict[str, Any]:
    """把检索诊断翻成工具响应里的降级标记。

    正常时返回**空 dict**，而不是 ``{"retrieval_degraded": []}``：没有降级就不该
    在响应里多一个字段。让"字段出现"本身携带信息，调用方只需判断键在不在，
    不必再理解空数组的语义。
    """
    if diags is None or not getattr(diags, "is_degraded", False):
        return {}
    return {
        "retrieval_degraded": list(diags.degraded_legs),
        "retrieval_note": (
            diags.notes[0] if diags.notes else "检索能力不完整，本次排序可信度下降，请核对原文。"
        ),
    }


def _summary_from_chunks(chunks: list[dict], kb_id: str, diags: Any = None) -> str:
    head = chunks[0]
    section = head.get("section") or "未标注章节"
    source = head.get("source") or "未知文档"
    preview = str(head.get("content", "")).strip().replace("\n", " ")[:180]
    body = f"命中 {len(chunks)} 条制度片段（kb={kb_id}）：{source} / {section} — {preview}"
    prefix = ""
    if diags is not None and getattr(diags, "is_degraded", False):
        # 摘要是最可能被模型/人直接读到的字段，降级必须先在这里说一次：
        # 只在结构字段里标降级，等于假设调用方一定会检查那个字段。
        note = diags.notes[0] if diags.notes else "检索能力不完整"
        prefix += f"【注意】{note}"
    if not _has_authoritative_source(chunks):
        # 命中里一条"能当依据"的都没有 —— 这跟"没查到"是两件不同的事：
        # 很可能查到了，但查到的全是第三方模板/无关材料。必须在同一句话里说清，
        # 否则模型会照旧把排第一的片段当制度讲出去。
        prefix += (
            "【注意】本次命中里没有国家法规或本单位制度，"
            f"只有{_authority_label(chunks[0].get('authority'))}等非权威来源，"
            "不能作为制度依据回答。"
        )
    return f"{prefix}{body}" if prefix else body


#: 来源级别 → 面向 HR 的说法。工具响应里不能出现 national_law / vendor_template
#: 这类只有实现者才懂的取值（与 ``_LEG_LABEL`` 同一约定）。
_AUTHORITY_LABEL: dict[str, str] = {
    "national_law": "国家法规",
    "company_policy": "本单位制度",
    "vendor_template": "第三方模板",
    "reference": "参考资料",
    "unknown": "未标注来源的文件",
}


def _authority_label(value: Any) -> str:
    return _AUTHORITY_LABEL.get(str(value or "unknown"), _AUTHORITY_LABEL["unknown"])


def _has_authoritative_source(chunks: list[dict]) -> bool:
    """命中里有没有"能回答依据是什么"的来源（国家法规 / 本单位制度）。"""
    return any(str(c.get("authority") or "") in AUTHORITATIVE_AUTHORITIES for c in chunks)


def _authority_payload(chunks: list[dict]) -> dict[str, Any]:
    """命中**完全没有**权威来源时，在响应里显式标出来。

    与 ``_degradation_payload`` 同一约定：正常情况返回空 dict，让"字段出现"本身
    携带信息。这里不逐条暴露来源级别（那会让每次响应都变长），只在最危险的那种
    情况下——手里一条依据都没有——明确说没有。
    """
    if _has_authoritative_source(chunks):
        return {}
    labels = sorted({_authority_label(c.get("authority")) for c in chunks})
    return {
        "authoritative_source": False,
        "authority_note": (
            f"命中 {len(chunks)} 条片段，但没有任何一条来自国家法规或本单位制度，"
            f"来源性质为：{'、'.join(labels)}。这些材料不能作为制度依据，"
            "应视为无依据，或先补充本单位制度文件。"
        ),
    }


#: concise 档的单片段正文预算。detailed 档不复用这个值 —— 它的用途是
#: 「核对条款完整措辞」，截到 300 字会破坏这一用途；detailed 的长度由
#: app.mcp.budgets 的总闸兜底。
_CONCISE_SNIPPET_CHARS = 300


def _chunk_view(chunk: dict, detail: str) -> dict[str, Any]:
    """检索片段按档位裁剪（任务书 T4-3）。

    concise：出处（source/section）+ 片段正文 —— 引用式问答需要的全部。
    detailed：额外带 chunk_id/document_id/kb_id 与未截断的 content —— 只有
    需要后续按 id 精读（get_policy_source）或落库对账时才值得多花这些 token。

    ``source_type`` 两档都带：它是"这条能不能当依据"的唯一线索，而知识库里
    同时存在国家法规、本单位制度和第三方模板 —— 少了它，调用方只看文档名
    无法分辨，而这正是曾经把酒店考核表当成"公司年假规定"的直接原因。
    """
    if detail == "concise":
        return {
            "source": chunk.get("source"),
            "section": chunk.get("section"),
            "source_type": _authority_label(chunk.get("authority")),
            "content": str(chunk.get("content", ""))[:_CONCISE_SNIPPET_CHARS],
        }
    view = dict(chunk)
    view["source_type"] = _authority_label(chunk.get("authority"))
    return view


async def execute_search_policy(params: dict) -> dict:
    """Hybrid RAG search over the tenant's policy knowledge base.

    用 ``retrieve_with_diagnostics`` 而不是 ``retrieve``：降级只写日志是不够的。
    2026-09-16 的真实事故里，dense 腿因 embedding 401 静默消失后本工具照回
    ``outcome: FOUND`` + 一段自信摘要，调用方完全看不出排序已经退化成纯关键词，
    结果把《职工带薪年休假条例》换成了字面命中"年""假"的酒店考核表。
    降级必须写进响应契约，否则"可见"只对运维成立、对作答者不成立。
    """
    tenant_id = _require_tenant()
    kb_id = await _resolve_kb_id(tenant_id, params.get("kb_id"))
    top_k = int(params.get("top_k") or 3)
    detail = str(params.get("detail") or "concise")
    authoritative_only = bool(params.get("authoritative_only"))

    from app.rag.retrieval.retriever import Retriever

    try:
        chunks, diags = await Retriever().retrieve_with_diagnostics(
            query=str(params["query"]),
            kb_id=kb_id,
            top_k=top_k,
            tenant_id=tenant_id,
        )
    except Exception as e:  # infrastructure failure → retry then human handoff
        logger.warning("read_tool_retrieval_failed", tool="search_policy", kb_id=kb_id, error=repr(e))
        raise ToolError("RETRIEVAL_UNAVAILABLE", f"{type(e).__name__}: {e}") from e

    # 零命中分支也带上降级标记：否则"两腿都正常但确实没有"和
    # "关键词腿也没命中"看起来一模一样，而后者并不说明库里没有该制度。
    degradation = _degradation_payload(diags)

    if not chunks:
        return {
            "summary": f"未命中制度片段（kb={kb_id}）：检索通道可用但无匹配，应按无依据处理",
            "chunks": [],
            "kb_id": kb_id,
            **degradation,
        }

    if authoritative_only:
        # 调用方只要"能当依据"的命中（国家法规 / 本单位制度）。
        # 过滤后为空**必须**显式说清"命中过但全被过滤掉了"——这与"真的没查到"
        # 是两种不同的无依据，混在一起会误导调用方去怀疑检索通道而不是语料。
        kept = [c for c in chunks if str(c.get("authority") or "") in AUTHORITATIVE_AUTHORITIES]
        if not kept:
            labels = sorted({_authority_label(c.get("authority")) for c in chunks})
            return {
                "summary": (
                    f"命中 {len(chunks)} 条，但均为{'、'.join(labels)}，"
                    "开启仅权威来源过滤后无剩余片段，应按无依据处理"
                ),
                "chunks": [],
                "kb_id": kb_id,
                "detail": detail,
                "authoritative_source": False,
                "authority_note": (
                    "开启 authoritative_only 后没有剩余命中：知识库检索到了内容，"
                    "但没有一条来自本单位制度或国家法规。"
                ),
                **degradation,
            }
        chunks = kept

    return {
        "summary": _summary_from_chunks(chunks, kb_id, diags=diags),
        "chunks": [_chunk_view(c, detail) for c in chunks[:top_k]],
        "kb_id": kb_id,
        "detail": detail,
        **_authority_payload(chunks),
        **degradation,
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
    """当前凭据的身份摘要、可用范围，以及**自己接过的助手**。

    ``params`` 只用于满足签名；真正的输入是绑定在上下文里的主体，**不接受**由调用
    方传入身份（那会把"你是谁"重新变成可以伪造的参数）。

    legacy agent loop 也会注册这个执行器，但那条路径不绑定主体 —— 那时这里会抛
    ``ToolError``，这是正确的：循环里的读步骤没有"外部 Agent 凭据"这个概念。

    安装实例是这里**唯一**要查库的部分（其余字段全在已绑定的主体里）。查询失败时
    降级为不返回该字段，而不是把整条调用变成 INTERNAL_ERROR —— 那样用户连"我是谁、
    能做什么"都看不到了，代价远大于少一个列表。
    """
    from app.mcp.installations import list_installations
    from app.mcp.profile import access_profile
    from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

    _ = params
    # 运行时 isinstance 需要真实类对象；顶层导入会与 app.mcp.* 成环，所以延迟到这里
    # （与 _require_principal 同一手法）。顺带把 current_read_principal() 的 ``object``
    # 收窄成 McpPrincipal —— 下面要用它的 tenant_id / user_id 查安装实例。
    from app.mcp.auth.principal import McpPrincipal

    bound = current_read_principal()
    if not isinstance(bound, McpPrincipal):
        raise ToolError(
            "PRINCIPAL_CONTEXT_MISSING",
            "access profile requires a bound principal (MCP transports bind it)",
        )
    principal = bound

    installations: list[dict] | None = None
    try:
        # 用 make_tenant_session 而不是自己拼 session：它把 RLS 的租户上下文与预热
        # 一起做了，是本仓库"非请求路径"的既有做法（本文件其余几处也用它）。
        session = await make_tenant_session(principal.tenant_id)
        try:
            records = await list_installations(
                session,
                principal.tenant_id,
                user_id=principal.user_id,
                active_only=True,
            )
            installations = [record.model_dump(mode="json") for record in records]
        finally:
            await session.close()
    except Exception:
        logger.exception("access_profile_installations_unavailable", tenant_id=principal.tenant_id)

    return access_profile(principal, TOOL_CATALOG, installations=installations)


def _require_principal() -> McpPrincipal:
    """取当前已绑定主体。缺失即失败 —— 案件相关工具没有"匿名降级"这一档。"""

    # 运行时 isinstance 需要真实的类对象；顶层导入会与 app.mcp.* 循环，
    # 所以延迟到这里。之前这里引用 TYPE_CHECKING 下的名字，运行时是 NameError
    # —— 任何走 execute_search_cases 的真实调用都会变成 INTERNAL_ERROR。
    from app.mcp.auth.principal import McpPrincipal

    principal = current_read_principal()
    if not isinstance(principal, McpPrincipal):
        raise ToolError(
            "PRINCIPAL_CONTEXT_MISSING",
            "case tools require a verified principal (MCP transports bind it)",
        )
    return principal


def _case_view(case: HRCase, detail: str = "concise") -> dict[str, Any]:
    """案件的最小字段集。

    **不含** ``description``：那是自由文本，通常写着员工的具体情况。列表的用途是
    "找到那个案件"，不是"读完它"。也不含任何真实员工标识 —— ``subject_ref`` 是
    合成引用，本来就是为这个用途设计的。

    ``case_id`` 在两个档位都保留：它是后续调用（get_case_summary / 提交办理）
    的必要键，concise 档省掉它会让"找到案件"这个列表用途失效。detailed 档
    额外带 created_at，用于人工对账与排序核对。
    """
    view: dict[str, Any] = {
        "case_id": case.id,
        "title": case.title,
        "subject_ref": case.subject_ref,
        "category": case.category,
        "risk_level": case.risk_level,
        "status": case.status,
    }
    if detail == "detailed":
        view["created_at"] = case.created_at.isoformat() if case.created_at else None
    return view


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


async def _visible_user_ids(principal: McpPrincipal) -> set[str]:
    """Use the same object-scope resolver as the REST HR Case surface."""
    from app.access.object_scope import resolve_visible_user_ids

    return await resolve_visible_user_ids(principal.tenant_id, principal.user_id, principal.role)


async def execute_search_cases(params: dict) -> dict:
    """按 ACL 搜索案件。跨租户与越权案件在这里就不可见 —— 不是"返回空摘要"。

    分页（任务书 T4-1）：多取一条（``limit + 1``）来判断 ``has_more``；返回
    ``next_cursor``（= 下一页的 offset）供客户端直接回传。**永不返回全量**：
    schema 的 limit 上限 20 + offset 上限 980 一起钳住最大窗口。
    """
    from app.scenarios.hr_case_agent.service import HRCaseService

    tenant_id = _require_tenant()
    principal = _require_principal()
    visible_user_ids = await _visible_user_ids(principal)

    limit = int(params.get("limit") or 20)
    offset = int(params.get("offset") or 0)
    detail = str(params.get("detail") or "concise")

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal), visible_user_ids=visible_user_ids)
        cases = await service.search_cases(
            limit=limit + 1,
            offset=offset,
            status=params.get("status"),
            category=params.get("category"),
        )
    finally:
        await session.close()

    has_more = len(cases) > limit
    cases = cases[:limit]
    if not cases:
        return {
            "summary": "没有找到你有权查看的案件。",
            "cases": [],
            "count": 0,
            "has_more": False,
        }
    result: dict[str, Any] = {
        "summary": f"找到 {len(cases)} 个案件（从第 {offset + 1} 条起）。",
        "cases": [_case_view(case, detail) for case in cases],
        "count": len(cases),
        "has_more": has_more,
    }
    if has_more:
        # 截断必须留痕并给出下一步（T4-2）：静默截断会让模型以为拿到了全部。
        result["next_cursor"] = offset + limit
        result["pagination_note"] = (
            f"还有更多案件未显示（本次最多 {limit} 条）。把 next_cursor={offset + limit} "
            "作为 offset 回传可取下一页；也可以用 status / category 缩小范围。"
        )
    return result


async def execute_get_case_summary(params: dict) -> dict:
    """读取单个有权案件的上下文。看不见的案件按"不存在"处理（不泄漏存在性）。"""
    from app.scenarios.hr_case_agent.service import HRCaseService
    from app.shared.errors import NotFoundError

    tenant_id = _require_tenant()
    principal = _require_principal()
    visible_user_ids = await _visible_user_ids(principal)
    case_id = str(params["case_id"])

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal), visible_user_ids=visible_user_ids)
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
    visible_user_ids = await _visible_user_ids(principal)
    case_id = str(params["case_id"])

    session = await make_tenant_session(tenant_id)
    try:
        service = HRCaseService(session, tenant_id, actor=_actor_label(principal), visible_user_ids=visible_user_ids)
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
