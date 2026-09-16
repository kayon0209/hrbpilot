"""HRBP AI Workbench — Policy QA schemas (request + response)."""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Request body for /api/policy-qa/ask."""

    question: str = Field(..., min_length=1, max_length=2000, description="用户提问内容")
    session_id: str | None = Field(None, description="会话ID，用于多轮对话")
    stream: bool = Field(True, description="是否使用 SSE 流式响应")


class CitationSource(BaseModel):
    """Single citation source in a QA response."""

    document_name: str = Field(..., description="制度文档名称")
    section: str = Field(..., description="章节号")
    content_snippet: str = Field(..., description="引用原文片段")
    confidence: float = Field(..., ge=0.0, le=1.0, description="相关度置信度")


class QAResponse(BaseModel):
    """Structured response for Policy QA scenario."""

    answer: str = Field(..., description="回答内容")
    citations: list[CitationSource] = Field(default_factory=list, description="引用来源列表")
    confidence: float = Field(..., ge=0.0, le=1.0, description="整体置信度")
    has_evidence: bool = Field(..., description="是否在知识库找到依据")
    #: 本次检索降级的腿（空 = 两条腿都参与了融合）。
    #:
    #: 为什么必须在响应里而不是只写日志：混合检索的 dense 与 sparse 是独立两条腿，
    #: 一条挂掉后另一条会继续作答（这是有意的设计）。但降级会改变**排序依据** ——
    #: 只剩 sparse 时排序退化成纯关键词匹配，字面命中高频词的片段会压过语义上真正
    #: 相关的那一份。2026-09-16 事故里，这一点让《职工带薪年休假条例》被一份酒店
    #: 技能考核表顶掉，而接口照常返回 answer + 高 confidence。
    #:
    #: 只在**依赖真的坏了**时出现；腿正常返回空（窄查询）不算降级。
    retrieval_degraded: list[str] = Field(
        default_factory=list, description="本次检索不可用的通道（空表示完整）"
    )
    retrieval_note: str | None = Field(
        None, description="降级的人话说明；未降级时为 None"
    )
    guardrail_flags: dict = Field(default_factory=dict, description="护栏触发记录")
    latency_ms: int = Field(0, description="响应耗时(ms)")
    tokens_used: int | None = Field(None, description="LLM token 消耗")


class SSEEvent(BaseModel):
    """SSE event data for streaming responses."""

    event: str  # "chunk" | "done" | "error" | "sources" | "meta"
    data: str  # JSON-encoded payload
