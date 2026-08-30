"""HRBP AI Workbench — all ORM models imported here for Alembic auto-detect."""

from app.data.models.access_scope import ManagerOrgScope, OrgUnit
from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.data_source import DataSource
from app.data.models.hr_case import (
    AgentRun,
    ApprovalRequest,
    CaseEvent,
    CasePlan,
    HRCase,
    ToolExecution,
)
from app.data.models.infra import AsyncTask, AuditLog, EvalResult, TokenLedgerEntry
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.data.models.scenarios import (
    CultureContent,
    EmployeeRequest,
    InsightReport,
    InterviewDigest,
    KnowledgeFeedbackCandidate,
    WeeklyReport,
)
from app.data.models.tenant import Tenant
from app.data.models.user import User

__all__ = [
    "AgentRun",
    "ApprovalRequest",
    "AsyncTask",
    "AuditLog",
    "Base",
    "CaseEvent",
    "CasePlan",
    "ChatMessage",
    "ChatSession",
    "CultureContent",
    "DataSource",
    "Document",
    "DocumentChunk",
    "EmployeeRequest",
    "EvalResult",
    "HRCase",
    "InsightReport",
    "InterviewDigest",
    "KnowledgeBase",
    "KnowledgeFeedbackCandidate",
    "ManagerOrgScope",
    "OrgUnit",
    "Tenant",
    "TenantMixin",
    "TimestampMixin",
    "TokenLedgerEntry",
    "ToolExecution",
    "UUIDPrimaryKey",
    "User",
    "WeeklyReport",
]
