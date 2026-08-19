"""HRBP AI Workbench — Policy QA API routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db, tenant_session
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.knowledge_base import KnowledgeBase
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.scenarios.policy_qa.schemas import QAResponse
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/policy-qa", tags=["policy-qa"])

orchestrator = PolicyQAOrchestrator()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    kb_id: str | None = Field(None, description="Knowledge base UUID; defaults to scenario config")
    session_id: str | None = Field(None, description="Existing chat session ID")
    stream: bool = Field(True, description="Use SSE streaming")


class FeedbackBody(BaseModel):
    message_id: str = Field(..., min_length=1, description="对应回答的数据库 message_id")
    rating: str = Field(..., pattern="^(up|down)$")
    correction: str = ""


async def _resolve_policy_kb(session: AsyncSession, tenant_id: str, requested_kb_id: str | None) -> KnowledgeBase:
    stmt = select(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.scenario_id == "policy_qa", KnowledgeBase.status == "active")
    if requested_kb_id:
        stmt = stmt.where(KnowledgeBase.id == requested_kb_id)
    else:
        stmt = stmt.order_by(KnowledgeBase.created_at.asc()).limit(1)
    kb = (await session.execute(stmt)).scalars().first()
    if kb is None:
        raise NotFoundError("Active policy knowledge base", requested_kb_id or tenant_id)
    return kb


async def _get_or_create_chat_session(session: AsyncSession, tenant_id: str, user_id: str, session_id: str | None) -> ChatSession:
    if session_id:
        existing = (
            (
                await session.execute(select(ChatSession).where(ChatSession.id == session_id, ChatSession.tenant_id == tenant_id, ChatSession.user_id == user_id, ChatSession.scenario_id == "policy_qa"))
            )
            .scalars()
            .first()
        )
        if existing:
            return existing
    chat_session = ChatSession(tenant_id=tenant_id, user_id=user_id, scenario_id="policy_qa")
    session.add(chat_session)
    await session.flush()
    return chat_session


async def _add_message(session: AsyncSession, session_id: str, role: str, content: str, confidence: float | None = None, citations_json: str | None = None) -> ChatMessage:
    message = ChatMessage(session_id=session_id, role=role, content=content, confidence=confidence, citations_json=citations_json)
    session.add(message)
    await session.flush()
    return message


@router.post("/ask")
@require_auth
async def ask_question(body: AskRequest, request: Request, session: AsyncSession = Depends(get_db)):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    kb = await _resolve_policy_kb(session, tenant_id, body.kb_id)
    await session.close()

    result = await orchestrator.execute(body.question, tenant_id=tenant_id, user_id=user_id, kb_id=kb.id)
    chat_session_id = body.session_id
    async with tenant_session(tenant_id) as db:
        chat_session = await _get_or_create_chat_session(db, tenant_id, user_id, chat_session_id)
        await _add_message(db, chat_session.id, "user", body.question)
        assistant_message = await _add_message(db, chat_session.id, "assistant", result.answer, confidence=result.confidence)
        await db.commit()

    response = result.model_dump()
    response["message_id"] = assistant_message.id
    response["session_id"] = chat_session.id
    return response
