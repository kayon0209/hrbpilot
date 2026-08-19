"""HRBP AI Workbench — Authentication API routes.

/login   → authenticate, return access + refresh tokens
/refresh → exchange refresh token for new access token
/me      → return current user profile

In development mode (when PostgreSQL is unreachable), falls back to mock users.
"""

import datetime

from fastapi import APIRouter, Request
from jose import jwt
from pydantic import BaseModel

from app.config.settings import settings
from app.shared.dev_mock_users import (
    get_mock_user_by_email,
    get_mock_user_by_id,
    verify_mock_password,
)
from app.shared.errors import AuthError, DatabaseError, ExternalServiceError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# DB availability cache with TTL so the system auto-recovers after DB restart.
_db_available: bool | None = None
_db_checked_at: float = 0.0
_DB_RECHECK_INTERVAL: float = 10.0  # seconds before re-pinging a previously-unreachable DB


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


def _create_access_token(user_id: str, role: str, tenant_id: str, email: str) -> str:
    """Generate short-lived JWT access token."""
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=settings.jwt_access_expires_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "email": email,
        "exp": expires,
        "iat": datetime.datetime.now(datetime.UTC),
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def _create_refresh_token(user_id: str, tenant_id: str) -> str:
    """Generate long-lived refresh token."""
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=settings.jwt_refresh_expires_days)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "exp": expires,
        "iat": datetime.datetime.now(datetime.UTC),
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginBody):
    """Authenticate user with email + password, return JWT tokens."""
    db_ok = await _check_db_available()

    if db_ok:
        # Use real database
        from passlib.context import CryptContext

        from app.data.database import get_db_session
        from app.data.repositories.user_repo import UserRepository

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

        try:
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
    else:
        if settings.is_production:
            raise ExternalServiceError("database", "Authentication database is unavailable")
        # Dev mode: use mock users
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
    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.JWTError:
        raise AuthError("Invalid or expired refresh token") from None

    if payload.get("type") != "refresh":
        raise AuthError("Not a refresh token")

    user_id = payload.get("sub")
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
    else:
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
    else:
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
    if settings.is_production:
        raise NotFoundError("Endpoint", "dev-users")

    from app.shared.dev_mock_users import _MOCK_USERS

    # Passwords are never returned — even in dev mode. The frontend login form
    # requires a real password (default dev password documented in README only).
    return {
        "users": [
            {"email": u.email, "name": u.name, "role": u.role, "password_required": True}
            for u in _MOCK_USERS.values()
        ],
    }
