"""Culture generation must not report a fake saved object after database failure.

The history-path check reads the culture_contents table via a live PostgreSQL,
hence integration.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from starlette.requests import Request

from app.access.routes import culture_content
from app.config.settings import settings
from app.main import create_app
from app.scenarios.culture_content.schemas import CultureContentResponse, GenerateContentRequest
from app.shared.errors import DatabaseError

pytestmark = pytest.mark.integration


def _hrbp_token(tenant_id: str, user_id: str) -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": user_id,
                "role": "hrbp",
                "tenant_id": tenant_id,
                "email": f"{user_id}@example.test",
                "type": "access",
                "jti": str(uuid4()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "exp": now + timedelta(minutes=15),
                "iat": now,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


@pytest.mark.asyncio
async def test_generate_fails_when_content_cannot_be_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restoring the random in-memory fallback would make this return a fake content ID."""
    generated = CultureContentResponse(
        news_article="已生成内容",
        group_notice="群通知",
        employee_story="员工故事",
        event_copy="活动文案",
        keywords_used=["协作"],
        tone="务实",
    )

    async def generate(**_kwargs: object) -> CultureContentResponse:
        return generated

    async def fail_to_store(**_kwargs: object) -> str:
        return ""

    monkeypatch.setattr(culture_content.orchestrator, "generate", generate)
    monkeypatch.setattr(culture_content.orchestrator, "_store_content", fail_to_store)

    request = Request({"type": "http", "method": "POST", "path": "/api/culture-content/generate", "headers": []})
    request.state.tenant_id = "tenant-a"
    request.state.user_id = "user-a"
    request.state.user_role = "hrbp"

    with pytest.raises(DatabaseError, match="文化内容未能保存"):
        await culture_content.generate_content(
            GenerateContentRequest(keywords=["协作"], tone="务实", expand_keywords=False),
            request,
        )


def test_history_path_reaches_the_history_handler() -> None:
    """Putting /{content_id} before /history makes a valid history request return 404."""
    tenant_id, user_id = str(uuid4()), str(uuid4())
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/culture-content/history",
            headers={"Authorization": f"Bearer {_hrbp_token(tenant_id, user_id)}"},
        )

    assert response.status_code == 200
    assert response.json() == {"contents": [], "total": 0}
