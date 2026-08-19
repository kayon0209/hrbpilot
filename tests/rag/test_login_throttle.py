import pytest
from starlette.requests import Request

from app.access.routes import auth
from app.shared.errors import RateLimitError


def _login_request(ip: str = "10.0.0.1") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [],
        "client": (ip, 0),
    })


@pytest.mark.asyncio
async def test_login_throttle_blocks_after_limit() -> None:
    auth._LOGIN_ATTEMPTS.clear()
    request = _login_request()

    for _ in range(auth._LOGIN_LIMIT):
        await auth._check_login_rate_limit(request, "a@example.com")

    with pytest.raises(RateLimitError):
        await auth._check_login_rate_limit(request, "a@example.com")


@pytest.mark.asyncio
async def test_login_throttle_isolated_by_email() -> None:
    auth._LOGIN_ATTEMPTS.clear()
    request = _login_request()

    for _ in range(auth._LOGIN_LIMIT):
        await auth._check_login_rate_limit(request, "a@example.com")

    await auth._check_login_rate_limit(request, "b@example.com")
