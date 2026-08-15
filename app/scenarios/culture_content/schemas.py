"""HRBP AI Workbench — Culture Content schemas."""

from pydantic import BaseModel, Field


class CultureContentResponse(BaseModel):
    """Structured response for Culture Content scenario — 4 channel versions."""
    news_article: str = Field("", description="新闻稿 800-1200字")
    group_notice: str = Field("", description="群通知 100-200字")
    employee_story: str = Field("", description="员工故事 500-800字")
    event_copy: str = Field("", description="活动文案 200-400字")
    keywords_used: list[str] = Field(default_factory=list, description="实际使用的关键词")
    tone: str = Field("", description="整体基调")
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class GenerateContentRequest(BaseModel):
    """Request to generate culture content."""
    keywords: list[str] = Field(..., min_length=1, description="关键词列表")
    tone: str = Field("积极向上", description="期望基调")
    expand_keywords: bool = Field(True, description="是否自动扩展关键词")


class KeywordExpansionResponse(BaseModel):
    """Expanded keywords for culture content."""
    original: list[str]
    expanded: list[str]
    categories: dict[str, list[str]]  # {"正面价值观": [...], "活动主题": [...]}
