from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.access.routes import auth
from app.config.settings import Settings


async def test_database_login_returns_tokens_on_valid_credentials(monkeypatch) -> None:
    user = SimpleNamespace(
        id="user-1",
        email="hr@example.com",
        role="hr_manager",
        tenant_id="tenant-1",
        hashed_password="hash",
    )

    async def db_available() -> bool:
        return True

    async def get_user(_self, email: str):
        assert email == user.email
        return user

    async def fake_session():
        yield SimpleNamespace()

    monkeypatch.setattr(auth, "_check_db_available", db_available)
    monkeypatch.setattr("app.data.database.get_db_session", fake_session)
    monkeypatch.setattr("app.data.repositories.user_repo.UserRepository.get_by_email", get_user)
    monkeypatch.setattr("passlib.context.CryptContext.verify", lambda *_args: True)

    result = await auth.login(auth.LoginBody(email=user.email, password="secret"))

    assert result.access_token
    assert result.refresh_token


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(PydanticValidationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="change-me-in-production")


def test_production_accepts_strong_jwt_secret() -> None:
    configured = Settings(app_env="production", jwt_secret="a" * 32)
    assert configured.is_production
