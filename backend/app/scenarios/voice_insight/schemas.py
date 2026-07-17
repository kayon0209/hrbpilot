"""HRBP AI Workbench — Voice Insight schemas."""

from pydantic import BaseModel, Field
from enum import Enum


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TrendDirection(str, Enum):
    UP = "上升"
    STABLE = "稳定"
    DOWN = "下降"


class Cluster(BaseModel):
    """A cluster of similar demands."""
    label: str = Field(..., description="集群标签")
    demand_count: int = Field(0, description="诉求数量")
    demands: list[str] = Field(default_factory=list, description="典型诉求列表")
    severity: Severity = Field(Severity.LOW, description="严重程度")


class RiskSignal(BaseModel):
    """A risk signal identified from voice data."""
    signal: str = Field(..., description="风险描述")
    severity: Severity = Field(Severity.MEDIUM, description="严重程度")
    source_ids: list[str] = Field(default_factory=list, description="来源编号")
    trend: TrendDirection = Field(TrendDirection.STABLE, description="趋势方向")


class Trend(BaseModel):
    """A trend identified from voice data."""
    topic: str = Field(..., description="主题")
    direction: TrendDirection = Field(TrendDirection.STABLE, description="方向")
    confidence: float = Field(0.5, description="置信度")
    evidence: str = Field("", description="依据")


class InsightReportResponse(BaseModel):
    """Structured response for Voice Insight scenario."""
    clusters: list[Cluster] = Field(default_factory=list)
    risk_signals: list[RiskSignal] = Field(default_factory=list)
    trends: list[Trend] = Field(default_factory=list)
    summary: str = Field("", description="整体洞察摘要")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    has_evidence: bool = Field(True)


class AnalyzeRequest(BaseModel):
    """Request to start voice analysis."""
    document_ids: list[str] = Field(default_factory=list, description="待分析文档ID列表")
    period: str = Field("", description="分析时间段, 如 2026-Q2")


class TaskStatusResponse(BaseModel):
    """Status of an async analysis task."""
    task_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0
    result: InsightReportResponse | None = None
    error: str | None = None
