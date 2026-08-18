"""HRBP AI Workbench — Feedback and metrics API."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


class FeedbackBody(BaseModel):
    message_id: str = ""
    rating: str = "up"
    correction: str = ""


@router.post("/feedback")
async def submit_feedback(request: Request, body: FeedbackBody):
    """Record user feedback (thumbs up/down, correction annotations)."""
    logger.info("feedback_received", rating=body.rating, message_id=body.message_id)
    return {"status": "ok", "rating": body.rating}
