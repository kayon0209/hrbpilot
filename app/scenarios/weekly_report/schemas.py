"""HRBP AI Workbench — Weekly Report schemas."""

from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    COMPLETED = "已完成"
    IN_PROGRESS = "进行中"
    PENDING = "待启动"


class Priority(str, Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ProgressItem(BaseModel):
    item: str = Field(..., description="进展条目")
    source: str = Field("", description="数据来源")
    status: TaskStatus = Field(TaskStatus.IN_PROGRESS)


class RiskItem(BaseModel):
    risk: str = Field(..., description="风险描述")
    severity: Severity = Field(Severity.MEDIUM)
    owner: str = Field("", description="跟进人")
    action: str = Field("", description="应对措施")


class PlanItem(BaseModel):
    task: str = Field(..., description="下周计划")
    priority: Priority = Field(Priority.MEDIUM)
    deadline: str = Field("", description="截止日期")


class WeeklyReportResponse(BaseModel):
    """Structured response for Weekly Report scenario."""
    period: str = Field("", description="报告周期")
    summary: str = Field("", description="整体摘要")
    progress: list[ProgressItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    plan: list[PlanItem] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    """Request to generate a weekly report."""
    period: str = Field(..., description="报告周期, 如 2026-W28")
    source_ids: list[str] = Field(default_factory=list, description="数据源ID列表")
    draft_mode: bool = Field(True, description="是否生成草稿（可编辑）")


class SaveRequest(BaseModel):
    """Request to save/publish a weekly report."""
    report_id: str
    action: str = "save"  # save | publish
    edits: dict | None = None  # Optional manual edits
