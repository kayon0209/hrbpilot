from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError
from starlette.requests import Request

from app.access.middleware.tenant import require_tenant_id
from app.access.routes import auth
from app.config.settings import Settings
from app.guardrails.output_guard import OutputGuardrail
from app.shared.errors import AuthError


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

    auth._LOGIN_ATTEMPTS.clear()
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/auth/login", "headers": [], "client": ("127.0.0.1", 0)}
    )

    monkeypatch.setattr(auth, "_check_db_available", db_available)
    monkeypatch.setattr("app.data.database.get_db_session", fake_session)
    monkeypatch.setattr("app.data.repositories.user_repo.UserRepository.get_by_email", get_user)
    monkeypatch.setattr("passlib.context.CryptContext.verify", lambda *_args: True)

    result = await auth.login(auth.LoginBody(email=user.email, password="secret"), request)

    assert result.access_token
    assert result.refresh_token


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(PydanticValidationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="change-me-in-production")


def test_production_accepts_strong_jwt_secret() -> None:
    configured = Settings(
        _env_file=None,  # hermetic: don't inherit machine-local .env (e.g. VECTOR_DB_PORT)
        app_env="production",
        jwt_secret="a" * 32,
        llm_api_key="configured-llm-key",
        embedding_api_key="configured-embedding-key",
        embedding_base_url="https://embedding.example/v1",
        minio_access_key="configured-minio-key",
        minio_secret_key="configured-minio-secret",
    )
    assert configured.is_production
    assert configured.milvus_endpoint == "http://localhost:19530"


def test_production_rejects_missing_llm_or_embedding_configuration() -> None:
    with pytest.raises(PydanticValidationError, match="LLM_API_KEY"):
        Settings(
            app_env="production",
            jwt_secret="a" * 32,
            llm_api_key="change-me",
            embedding_api_key="",
            embedding_base_url="",
            minio_access_key="configured-minio-key",
            minio_secret_key="configured-minio-secret",
        )


def test_request_without_tenant_context_fails_closed() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    with pytest.raises(AuthError, match="Tenant context is required"):
        require_tenant_id(request)

    request.state.tenant_id = "tenant-1"
    assert require_tenant_id(request) == "tenant-1"


@pytest.mark.asyncio
async def test_output_guard_desensitizes_pii() -> None:
    guard = OutputGuardrail()
    processed, flags = await guard.check("手机号 13812345678，身份证号 110105199001011234", ["pii_detection"], [])
    assert flags["pii_detected"] is True
    assert "[phone_已脱敏]" in processed or "[id_card_已脱敏]" in processed
