import pytest
from starlette.requests import Request

from app.access.routes import auth
from app.shared.errors import RateLimitError


@pytest.fixture(autouse=True)
def _clear_login_throttle_keys():
    """The throttle keeps sliding windows in Redis that outlive a test run;
    leftover keys from earlier runs would trip these tests. Clear them."""
    import asyncio

    import redis.asyncio as aioredis

    async def _flush():
        try:
            r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
            keys = await r.keys("login_attempts:*")
            if keys:
                await r.delete(*keys)
            await r.aclose()
        except Exception:
            pass  # redis unavailable: throttle falls back to memory

    asyncio.run(_flush())


def _login_request(ip: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": (ip, 0),
        }
    )


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
