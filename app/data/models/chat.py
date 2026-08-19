"""HRBP AI Workbench — Chat session and message models.

RLS enabled via tenant_id.
"""

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class ChatSession(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "chat_sessions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(50), nullable=False)  # policy_qa | interview | ...

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id} scenario={self.scenario_id}>"


class ChatMessage(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "chat_messages"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[str | None] = mapped_column(Text, default=None)  # JSON array of citation sources
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    feedback_rating: Mapped[str | None] = mapped_column(String(10), default=None)
    feedback_correction: Mapped[str | None] = mapped_column(Text, default=None)
    feedback_at: Mapped[float | None] = mapped_column(Float, default=None)

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} role={self.role} session={self.session_id}>"
