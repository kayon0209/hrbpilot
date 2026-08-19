"""HRBP AI Workbench — Policy QA API routes."""

from __future__ import annotations

import json
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


async def _save_history_async(tenant_id: str, user_id: str, question: str, result: QAResponse, session_id: str | None) -> str | None:
    async with tenant_session(tenant_id) as db:
        chat_session = await _get_or_create_chat_session(db, tenant_id, user_id, session_id)
        await _add_message(db, chat_session.id, "user", question)
        assistant_message = await _add_message(db, chat_session.id, "assistant", result.answer, confidence=result.confidence)
        await db.commit()
        return assistant_message.id


@router.post("/ask")
@require_auth
async def ask_question(body: AskRequest, request: Request, session: AsyncSession = Depends(get_db)):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    kb = await _resolve_policy_kb(session, tenant_id, body.kb_id)
    await session.close()

    if body.stream:
        async def event_stream():
            try:
                start = time.time()
                async for raw_event in orchestrator.execute_stream(body.question, tenant_id=tenant_id, user_id=user_id, kb_id=kb.id):
                    event = json.loads(raw_event)
                    if event.get("event") == "done":
                        try:
                            done_payload = json.loads(event.get("data", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            done_payload = {}
                        qa_result = QAResponse(
                            answer="",
                            citations=[],
                            confidence=float(done_payload.get("confidence", 0.0) or 0.0),
                            has_evidence=bool(done_payload.get("has_evidence", False)),
                            guardrail_flags=done_payload.get("guardrail_flags", {}),
                            latency_ms=int(done_payload.get("latency_ms", 0) or 0),
                            tokens_used=done_payload.get("tokens_used"),
                        )
                        message_id = await _save_history_async(tenant_id, user_id, body.question, qa_result, body.session_id)
                        done_payload["message_id"] = message_id
                        done_payload["latency_ms"] = int((time.time() - start) * 1000)
                        yield f"data: {json.dumps({'event': 'done', 'data': json.dumps(done_payload, ensure_ascii=False)})}\n\n"
                        continue
                    if event.get("event") == "error":
                        yield f"data: {raw_event}\n\n"
                        continue
                    yield f"data: {raw_event}\n\n"
            except Exception:
                logger.exception("policy_qa_sse_error")
                yield f"data: {json.dumps({'event': 'error', 'data': json.dumps({'message': '服务异常，请稍后重试'})})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await orchestrator.execute(body.question, tenant_id=tenant_id, user_id=user_id, kb_id=kb.id)
    message_id = await _save_history_async(tenant_id, user_id, body.question, result, body.session_id)
    response = result.model_dump()
    response["message_id"] = message_id
    return response
