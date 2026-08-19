"""HRBP AI Workbench — Policy QA API routes.

POST /api/policy-qa/ask → SSE streaming or JSON response
GET  /api/policy-qa/history → recent QA history
POST /api/policy-qa/feedback → thumbs up/down feedback on a response
"""

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
from app.data.database import get_db, make_tenant_session
from app.data.models.chat import ChatMessage, ChatSession
from app.data.models.knowledge_base import KnowledgeBase
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/policy-qa", tags=["policy-qa"])

orchestrator = PolicyQAOrchestrator()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    kb_id: str | None = Field(None, description="Knowledge base UUID; defaults to scenario config")
    stream: bool = Field(True, description="Use SSE streaming")


class FeedbackBody(BaseModel):
    message_id: str = Field(..., min_length=1, description="对应回答的数据库 message_id")
    rating: str = Field(..., pattern="^(up|down)$")
    correction: str = ""


async def _resolve_policy_kb(session: AsyncSession, tenant_id: str, requested_kb_id: str | None) -> KnowledgeBase:
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.tenant_id == tenant_id,
        KnowledgeBase.scenario_id == "policy_qa",
        KnowledgeBase.status == "active",
    )
    if requested_kb_id:
        stmt = stmt.where(KnowledgeBase.id == requested_kb_id)
    else:
        stmt = stmt.order_by(KnowledgeBase.created_at.asc()).limit(1)
    kb = (await session.execute(stmt)).scalars().first()
    if kb is None:
        raise NotFoundError("Active policy knowledge base", requested_kb_id or tenant_id)
    return kb


async def _get_or_create_chat_session(session: AsyncSession, tenant_id: str, user_id: str) -> ChatSession:
    """Create a fresh chat session for each policy QA turn.

    History is rendered as question + answer pairs, so reusing the latest
    session would mix the first question with the last answer across turns.
    """
    chat_session = ChatSession(tenant_id=tenant_id, user_id=user_id, scenario_id="policy_qa")
    session.add(chat_session)
    await session.flush()
    return chat_session


async def _add_message(
    session: AsyncSession,
    session_id: str,
    role: str,
    content: str,
    confidence: float | None = None,
    citations_json: str | None = None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        confidence=confidence,
        citations_json=citations_json,
    )
    session.add(message)
    await session.flush()
    return message


@router.post("/ask")
@require_auth
async def ask_question(
    body: AskRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Ask a policy question — supports both SSE streaming and JSON response."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    kb = await _resolve_policy_kb(session, tenant_id, body.kb_id)

    if body.stream:
        question_text = body.question

        async def event_stream():
            full_answer = ""
            done_payload: dict[str, object] | None = None
            try:
                async for sse_data in orchestrator.execute_stream(
                    question=question_text,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    kb_id=kb.id,
                ):
                    event = None
                    try:
                        event = json.loads(sse_data)
                    except (json.JSONDecodeError, TypeError):
                        pass

                    if isinstance(event, dict) and event.get("event") == "chunk":
                        try:
                            payload = json.loads(event.get("data", "{}"))
                            full_answer += payload.get("text", "")
                        except (json.JSONDecodeError, TypeError):
                            pass
                        yield f"data: {sse_data}\n\n"
                        continue

                    if isinstance(event, dict) and event.get("event") == "done":
                        try:
                            done_payload = json.loads(event.get("data", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            done_payload = {}
                        continue

                    yield f"data: {sse_data}\n\n"

                if full_answer.strip():
                    async with make_tenant_session(tenant_id) as db:
                        chat_session = await _get_or_create_chat_session(db, tenant_id, user_id)
                        await _add_message(db, chat_session.id, "user", question_text)
                        assistant_message = await _add_message(
                            db,
                            chat_session.id,
                            "assistant",
                            full_answer,
                            confidence=float(done_payload.get("confidence", 0.0)) if done_payload else None,
                        )
                        await db.commit()
                        message_id = assistant_message.id
                else:
                    message_id = None

                final_payload = {
                    "message_id": message_id,
                    "confidence": done_payload.get("confidence", 0.0) if done_payload else 0.0,
                    "has_evidence": done_payload.get("has_evidence", False) if done_payload else False,
                    "latency_ms": done_payload.get("latency_ms", 0) if done_payload else 0,
                    "guardrail_flags": done_payload.get("guardrail_flags", {}) if done_payload else {},
                }
                yield f"data: {json.dumps({'event': 'done', 'data': json.dumps(final_payload, ensure_ascii=False)})}\n\n"
            except Exception as e:
                logger.error("policy_qa_sse_error", error=str(e))
                error_event = json.dumps(
                    {
                        "event": "error",
                        "data": json.dumps({"message": f"服务异常: {e!s}"}),
                    }
                )
                yield f"data: {error_event}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await orchestrator.execute(
        question=body.question,
        tenant_id=tenant_id,
        user_id=user_id,
        kb_id=kb.id,
    )
    async with make_tenant_session(tenant_id) as db:
        chat_session = await _get_or_create_chat_session(db, tenant_id, user_id)
        await _add_message(db, chat_session.id, "user", body.question)
        assistant_message = await _add_message(
            db,
            chat_session.id,
            "assistant",
            result.answer,
            confidence=result.confidence,
        )
        await db.commit()
        message_id = assistant_message.id

    response = result.model_dump()
    response["message_id"] = message_id
    return response


@router.get("/knowledge-bases")
@require_auth
async def list_policy_knowledge_bases(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = require_tenant_id(request)
    rows = (
        (
            await session.execute(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.tenant_id == tenant_id,
                    KnowledgeBase.scenario_id == "policy_qa",
                    KnowledgeBase.status == "active",
                )
                .order_by(KnowledgeBase.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "knowledge_bases": [{"id": kb.id, "name": kb.name, "status": kb.status} for kb in rows],
        "total": len(rows),
    }


@router.get("/history")
@require_auth
async def get_history(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """Get recent policy QA history for the current user."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")

    session_rows = (
        (
            await session.execute(
                select(ChatSession)
                .where(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.user_id == user_id,
                    ChatSession.scenario_id == "policy_qa",
                )
                .order_by(ChatSession.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    session_ids = [chat_session.id for chat_session in session_rows]
    message_rows = []
    if session_ids:
        message_rows = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id.in_(session_ids))
                    .order_by(ChatMessage.session_id.asc(), ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    messages_by_session: dict[str, list[ChatMessage]] = {session_id: [] for session_id in session_ids}
    for message in message_rows:
        messages_by_session.setdefault(message.session_id, []).append(message)

    sessions = []
    for chat_session in session_rows:
        messages = messages_by_session.get(chat_session.id, [])
        question = next((m.content for m in messages if m.role == "user"), "")
        assistant_message = next((m for m in reversed(messages) if m.role == "assistant"), None)
        answer = assistant_message.content if assistant_message else ""
        sessions.append(
            {
                "session_id": chat_session.id,
                "message_id": assistant_message.id if assistant_message else None,
                "question": question,
                "answer": answer,
                "created_at": chat_session.created_at.isoformat() if chat_session.created_at else None,
                "updated_at": chat_session.updated_at.isoformat() if chat_session.updated_at else None,
            }
        )

    return {"sessions": sessions, "total": len(sessions), "user_id": user_id}


@router.post("/feedback")
@require_auth
async def submit_feedback(
    request: Request,
    body: FeedbackBody,
    session: AsyncSession = Depends(get_db),
):
    """Submit thumbs up/down feedback on a QA response."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")

    message_row = (
        (
            await session.execute(
                select(ChatMessage)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(
                    ChatMessage.id == body.message_id,
                    ChatMessage.role == "assistant",
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.user_id == user_id,
                    ChatSession.scenario_id == "policy_qa",
                )
            )
        )
        .scalars()
        .first()
    )
    if not message_row:
        raise NotFoundError("Message", body.message_id)

    message_row.feedback_rating = body.rating
    message_row.feedback_correction = body.correction or None
    message_row.feedback_at = time.time()
    await session.commit()

    logger.info(
        "feedback_received",
        rating=body.rating,
        message_id=body.message_id,
        has_correction=bool(body.correction.strip()),
    )
    return {"status": "received", "rating": body.rating, "message_id": body.message_id}
