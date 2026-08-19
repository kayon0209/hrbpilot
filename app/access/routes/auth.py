"""HRBP AI Workbench — Authentication API routes.

/login   → authenticate, return access + refresh tokens
/refresh → exchange refresh token for new access token
/me      → return current user profile

In development mode (when PostgreSQL is unreachable), falls back to mock users.
"""

from __future__ import annotations

import datetime
import time
from collections import OrderedDict

from fastapi import APIRouter, Request
from jose import jwt
from pydantic import BaseModel

from app.config.settings import settings
from app.shared.dev_mock_users import (
    get_mock_user_by_email,
    get_mock_user_by_id,
    verify_mock_password,
)
from app.shared.errors import AuthError, DatabaseError, ExternalServiceError, NotFoundError, RateLimitError
from app.shared.logger import get_logger
from app.shared.redis_client import get_redis

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# DB availability cache with TTL so the system auto-recovers after DB restart.
_db_available: bool | None = None
_db_checked_at: float = 0.0
_DB_RECHECK_INTERVAL: float = 10.0  # seconds before re-pinging a previously-unreachable DB
_LOGIN_ATTEMPTS: OrderedDict[str, list[float]] = OrderedDict()
_LOGIN_LIMIT = 8
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_KEYS = 2000
_JWT_ISSUER = settings.jwt_issuer
_JWT_AUDIENCE = settings.jwt_audience


async def _check_db_available() -> bool:
    """Check if the database is reachable.

    Once confirmed available, stays cached for the process lifetime. If the
    check fails, it will be retried after ``_DB_RECHECK_INTERVAL`` seconds so
    that a recovered database is picked up automatically.
    """
    import time

    global _db_available, _db_checked_at
    if _db_available is True:
        return True
    if _db_available is False and (time.monotonic() - _db_checked_at) < _DB_RECHECK_INTERVAL:
        return False

    try:
        from sqlalchemy import text

        from app.data.database import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        _db_available = True
    except Exception:
        _db_available = False
        _db_checked_at = time.monotonic()
        logger.warning("database_unavailable_dev_mode", msg="Using mock users for dev mode")
    return _db_available


class LoginBody(BaseModel):
    email: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int  # seconds


class UserProfile(BaseModel):
    id: str
    email: str
    name: str
    role: str
    tenant_id: str


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _create_token(
    user_id: str,
    role: str,
    tenant_id: str,
    email: str,
    token_type: str,
    expires_delta: datetime.timedelta,
) -> str:
    """Generate a signed JWT with explicit token type and audience claims."""
    expires = _now_utc() + expires_delta
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "email": email,
        "type": token_type,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "exp": expires,
        "iat": _now_utc(),
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def _create_access_token(user_id: str, role: str, tenant_id: str, email: str) -> str:
    return _create_token(
        user_id=user_id,
        role=role,
        tenant_id=tenant_id,
        email=email,
        token_type="access",
        expires_delta=datetime.timedelta(minutes=settings.jwt_access_expires_minutes),
    )


def _create_refresh_token(user_id: str, tenant_id: str) -> str:
    return _create_token(
        user_id=user_id,
        role="employee",
        tenant_id=tenant_id,
        email="",
        token_type="refresh",
        expires_delta=datetime.timedelta(days=settings.jwt_refresh_expires_days),
    )


def _decode_jwt(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=_JWT_AUDIENCE,
            issuer=_JWT_ISSUER,
        )
    except Exception as exc:  # pragma: no cover - jose error types vary by version
        raise AuthError("Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise AuthError(f"Not a {expected_type} token")

    return payload


def _dev_users_enabled() -> bool:
    return settings.app_env == "development" and settings.enable_dev_users


def _login_rate_limit_key(request: Request, email: str) -> str:
    client_ip = getattr(request.client, "host", "unknown") if request.client else "unknown"
    normalized_email = email.strip().lower()
    return f"{client_ip}:{normalized_email}"


async def _check_login_rate_limit_redis(key: str) -> bool:
    redis = await get_redis()
    if redis is None:
        return False

    now = int(time.time())
    window_start = now - int(_LOGIN_WINDOW_SECONDS)
    redis_key = f"login_attempts:{key}"
    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, int(_LOGIN_WINDOW_SECONDS) + 30)
        _removed, _added, count, _expired = await pipe.execute()
        if int(count or 0) > _LOGIN_LIMIT:
            raise RateLimitError("登录过于频繁，请稍后再试")
        return True
    except RateLimitError:
        raise
    except Exception as exc:
        logger.warning("login_rate_limit_redis_failed", error=str(exc), key=key)
        return False


def _check_login_rate_limit_memory(key: str) -> None:
    now = time.monotonic()
    attempts = [ts for ts in _LOGIN_ATTEMPTS.get(key, []) if now - ts < _LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    _LOGIN_ATTEMPTS[key] = attempts
    _LOGIN_ATTEMPTS.move_to_end(key)
    while len(_LOGIN_ATTEMPTS) > _LOGIN_MAX_KEYS:
        _LOGIN_ATTEMPTS.popitem(last=False)
    if len(attempts) > _LOGIN_LIMIT:
        raise RateLimitError("登录过于频繁，请稍后再试")


async def _check_login_rate_limit(request: Request, email: str) -> None:
    key = _login_rate_limit_key(request, email)
    if await _check_login_rate_limit_redis(key):
        return
    _check_login_rate_limit_memory(key)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginBody, request: Request):
    """Authenticate user with email + password, return JWT tokens."""
    await _check_login_rate_limit(request, body.email)
    db_ok = await _check_db_available()

    if db_ok:
        from passlib.context import CryptContext

        from app.data.database import get_db_session
        from app.data.repositories.user_repo import UserRepository

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

        try:
            user = None
            async for db in get_db_session():
                repo = UserRepository(db)
                user = await repo.get_by_email(body.email)
        except Exception as exc:
            logger.error("login_database_failed", error=str(exc))
            raise DatabaseError("Authentication database query failed") from exc

        if not user or not pwd_context.verify(body.password, user.hashed_password):
            logger.warning("login_failed", email=body.email)
            raise AuthError("Invalid email or password")

        access_token = _create_access_token(user.id, user.role, user.tenant_id, user.email)
        refresh_token = _create_refresh_token(user.id, user.tenant_id)
        logger.info("login_success", user_id=user.id, role=user.role)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.jwt_access_expires_minutes * 60,
        )

    if not _dev_users_enabled():
        raise ExternalServiceError("database", "Authentication database is unavailable")

    user = get_mock_user_by_email(body.email.lower())  # type: ignore[assignment]
    if not user or not verify_mock_password(body.password, user.hashed_password):
        logger.warning("login_failed_dev", email=body.email)
        raise AuthError("Invalid email or password")

    access_token = _create_access_token(user.id, user.role, user.tenant_id, user.email)
    refresh_token = _create_refresh_token(user.id, user.tenant_id)
    logger.info("login_success_dev", user_id=user.id, role=user.role)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_expires_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshBody):
    """Exchange refresh token for new access token."""
    payload = _decode_jwt(body.refresh_token, "refresh")

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("Invalid or expired refresh token")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise AuthError("Invalid or expired refresh token")

    db_ok = await _check_db_available()

    if db_ok:
        from app.data.database import get_db_session
        from app.data.repositories.user_repo import UserRepository

        async for db in get_db_session():
            repo = UserRepository(db)
            user = await repo.get_by_id(user_id)
            if not user:
                raise NotFoundError("User", user_id)

            access_token = _create_access_token(user.id, user.role, user.tenant_id, user.email)
            refresh_token = _create_refresh_token(user.id, user.tenant_id)
            return TokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=settings.jwt_access_expires_minutes * 60,
            )

    if not _dev_users_enabled():
        raise ExternalServiceError("database", "Authentication database is unavailable")

    user = get_mock_user_by_id(user_id)  # type: ignore[assignment]
    if not user:
        raise NotFoundError("User", user_id)

    access_token = _create_access_token(user.id, user.role, user.tenant_id, user.email)
    refresh_token = _create_refresh_token(user.id, user.tenant_id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_expires_minutes * 60,
    )


@router.get("/me", response_model=UserProfile)
async def get_profile(request: Request):
    """Return current user profile (requires valid access token)."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise AuthError("Not authenticated")

    db_ok = await _check_db_available()

    if db_ok:
        from app.data.database import get_db_session
        from app.data.repositories.user_repo import UserRepository

        async for db in get_db_session():
            repo = UserRepository(db)
            user = await repo.get_by_id(user_id)
            if not user:
                raise NotFoundError("User", user_id)
            return UserProfile(
                id=user.id,
                email=user.email,
                name=user.name,
                role=user.role,
                tenant_id=user.tenant_id,
            )

    if not _dev_users_enabled():
        raise ExternalServiceError("database", "Authentication database is unavailable")

    user = get_mock_user_by_id(user_id)  # type: ignore[assignment]
    if not user:
        raise NotFoundError("User", user_id)
    return UserProfile(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        tenant_id=user.tenant_id,
    )


@router.get("/dev-users")
async def list_dev_users():
    """List available dev users for quick login."""
    if not _dev_users_enabled():
        raise NotFoundError("Endpoint", "dev-users")

    from app.shared.dev_mock_users import _MOCK_USERS

    return {
        "users": [
            {"email": u.email, "name": u.name, "role": u.role, "password_required": True}
            for u in _MOCK_USERS.values()
        ],
    }
