"""HRBP AI Workbench — Policy QA API routes.

POST /api/policy-qa/ask → SSE streaming or JSON response
GET  /api/policy-qa/history → recent QA history
POST /api/policy-qa/feedback → thumbs up/down feedback on a response
"""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.scenarios.policy_qa.orchestrator import PolicyQAOrchestrator
from app.access.middleware.decorators import require_auth
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/policy-qa", tags=["policy-qa"])

orchestrator = PolicyQAOrchestrator()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    stream: bool = Field(True, description="Use SSE streaming")


class FeedbackBody(BaseModel):
    message_id: str = ""
    rating: str = Field(..., pattern="^(up|down)$")
    correction: str = ""


@router.post("/ask")
@require_auth
async def ask_question(
    body: AskRequest,
    request: Request,
):
    """Ask a policy question — supports both SSE streaming and JSON response."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    user_id = getattr(request.state, "user_id", "unknown")

    if body.stream:
        async def event_stream():
            try:
                async for sse_data in orchestrator.execute_stream(
                    question=body.question,
                    tenant_id=tenant_id,
                    user_id=user_id,
                ):
                    yield f"data: {sse_data}\n\n"
            except Exception as e:
                logger.error("policy_qa_sse_error", error=str(e))
                error_event = json.dumps({
                    "event": "error",
                    "data": json.dumps({"message": f"服务异常: {str(e)}"}),
                })
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
    else:
        result = await orchestrator.execute(
            question=body.question,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        return result


@router.get("/history")
@require_auth
async def get_history(
    request: Request,
    limit: int = 20,
):
    """Get recent policy QA history for the current user."""
    user_id = getattr(request.state, "user_id", "unknown")
    # In-memory history (no DB required for dev)
    return {"sessions": [], "total": 0, "user_id": user_id}


@router.post("/feedback")
@require_auth
async def submit_feedback(
    request: Request,
    body: FeedbackBody,
):
    """Submit thumbs up/down feedback on a QA response."""
    logger.info("feedback_received", rating=body.rating, message_id=body.message_id)
    return {"status": "received", "rating": body.rating}
