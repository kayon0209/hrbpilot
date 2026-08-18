"""HRBP AI Workbench — Policy QA API routes.

POST /api/policy-qa/ask → SSE streaming or JSON response
GET  /api/policy-qa/history → recent QA history
POST /api/policy-qa/feedback → thumbs up/down feedback on a response
"""

import json
import time
from collections import deque

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.knowledge_base import KnowledgeBase
from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/policy-qa", tags=["policy-qa"])

orchestrator = PolicyQAOrchestrator()

# In-memory QA history per user (process-scoped; not persisted across restarts).
# Capped to bound memory; production persistence should move to PostgreSQL.
_HISTORY_MAX = 200
_user_history: dict[str, deque] = {}


def _record_history(user_id: str, question: str, answer: str, message_id: str) -> None:
    history = _user_history.setdefault(user_id, deque(maxlen=_HISTORY_MAX))
    history.appendleft(
        {
            "message_id": message_id,
            "question": question,
            "answer": answer,
            "created_at": time.time(),
        }
    )


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    kb_id: str | None = Field(None, description="Knowledge base UUID; defaults to scenario config")
    stream: bool = Field(True, description="Use SSE streaming")


class FeedbackBody(BaseModel):
    message_id: str = Field(..., min_length=1, description="对应回答的事件标识（done 事件返回的 message_id）")
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
            message_id = ""
            try:
                async for sse_data in orchestrator.execute_stream(
                    question=question_text,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    kb_id=kb.id,
                ):
                    yield f"data: {sse_data}\n\n"
                    try:
                        event = json.loads(sse_data)
                        if event.get("event") == "chunk":
                            payload = json.loads(event.get("data", "{}"))
                            full_answer += payload.get("text", "")
                        elif event.get("event") == "done":
                            payload = json.loads(event.get("data", "{}"))
                            message_id = payload.get("message_id", "")
                            if not message_id:
                                message_id = f"msg_{user_id}_{int(time.time() * 1000)}"
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception as e:
                logger.error("policy_qa_sse_error", error=str(e))
                error_event = json.dumps(
                    {
                        "event": "error",
                        "data": json.dumps({"message": f"服务异常: {e!s}"}),
                    }
                )
                yield f"data: {error_event}\n\n"
            finally:
                if full_answer or message_id:
                    _record_history(user_id, question_text, full_answer, message_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        result = await orchestrator.execute(
            question=body.question,
            tenant_id=tenant_id,
            user_id=user_id,
            kb_id=kb.id,
        )
        message_id = f"msg_{user_id}_{int(time.time() * 1000)}"
        _record_history(user_id, body.question, result.answer, message_id)
        return result


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
):
    """Get recent policy QA history for the current user."""
    user_id = getattr(request.state, "user_id", "unknown")
    history = list(_user_history.get(user_id, []))[:limit]
    return {"sessions": history, "total": len(_user_history.get(user_id, [])), "user_id": user_id}


@router.post("/feedback")
@require_auth
async def submit_feedback(
    request: Request,
    body: FeedbackBody,
):
    """Submit thumbs up/down feedback on a QA response."""
    logger.info("feedback_received", rating=body.rating, message_id=body.message_id)
    return {"status": "received", "rating": body.rating, "message_id": body.message_id}
