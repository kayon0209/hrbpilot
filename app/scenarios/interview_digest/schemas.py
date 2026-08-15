"""HRBP AI Workbench — Interview Digest schemas."""

from pydantic import BaseModel, Field
from enum import Enum


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Urgency(str, Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class Demand(BaseModel):
    """Single employee demand extracted from interview."""
    demand: str = Field(..., description="员工诉求内容")
    category: str = Field(..., description="分类: 工作环境/薪酬福利/职业发展/团队关系/管理制度")
    urgency: Urgency = Field(..., description="紧迫程度")


class ActionItem(BaseModel):
    """Single actionable follow-up item."""
    action: str = Field(..., description="具体行动")
    owner: str = Field(..., description="建议负责人")
    deadline: str = Field(..., description="建议完成时间")


class InterviewDigestResponse(BaseModel):
    """Structured response for Interview Digest scenario."""
    employee_demands: list[Demand] = Field(default_factory=list)
    risk_level: RiskLevel = Field(RiskLevel.LOW, description="风险等级")
    risk_signals: list[str] = Field(default_factory=list, description="风险信号列表")
    action_items: list[ActionItem] = Field(default_factory=list, description="行动项列表")
    suggested_owner: str = Field("", description="建议跟进负责人")
    summary: str = Field("", description="整体摘要")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="抽取置信度")
    has_evidence: bool = Field(True, description="数据是否充足")


class UploadRequest(BaseModel):
    """Request body for interview document upload."""
    filename: str = Field(..., description="文件名")
    content_type: str = Field("text/plain", description="文件类型")


class DigestStatus(BaseModel):
    """Status of an interview digest task."""
    task_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0
    result: InterviewDigestResponse | None = None
    error: str | None = None
