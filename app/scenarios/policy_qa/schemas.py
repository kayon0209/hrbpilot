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
    guardrail_flags: dict = Field(default_factory=dict, description="护栏触发记录")
    latency_ms: int = Field(0, description="响应耗时(ms)")
    tokens_used: int | None = Field(None, description="LLM token 消耗")


class SSEEvent(BaseModel):
    """SSE event data for streaming responses."""

    event: str  # "chunk" | "done" | "error" | "sources" | "meta"
    data: str  # JSON-encoded payload
