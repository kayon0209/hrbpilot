"""HRBP AI Workbench — centralized configuration.

All config via environment variables, validated at startup (fail fast).
Uses Pydantic BaseSettings for type casting and validation.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _required_env(name: str) -> str:
    """Helper for docs — Pydantic Settings handles validation automatically."""
    return name


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "hrbp-ai-workbench"
    app_env: str = "development"  # development | staging | production
    app_port: int = 8000
    app_debug: bool = False

    # --- Database (PostgreSQL) ---
    database_url: str = "postgresql+asyncpg://hrbp:hrbp_password@localhost:5432/hrbp_workbench"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    redis_session_ttl: int = 900  # seconds (15 min)

    # --- Auth (JWT) ---
    jwt_secret: str = "change-me-in-production"  # MUST override in production
    jwt_access_expires_minutes: int = 15
    jwt_refresh_expires_days: int = 7
    jwt_algorithm: str = "HS256"

    # --- LLM (Primary) ---
    llm_provider: str = "deepseek"  # zhipu | deepseek | openai | anthropic
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: str = "change-me"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.3

    # --- LLM (Fallback: DeepSeek) ---
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # --- LLM (Fallback: OpenAI) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Embedding (cloud-only, OpenAI-compatible) ---
    # Works with SiliconFlow / DashScope / Jina / OpenAI — set EMBEDDING_BASE_URL,
    # EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSION to match the provider.
    embedding_provider: str = "openai_compatible"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""  # defaults to llm_api_key if empty
    embedding_base_url: str = ""  # e.g. https://api.siliconflow.cn/v1
    embedding_dimension: int = 1024
    embedding_device: str = "cpu"  # retained for compatibility; unused for cloud

    # --- Vector Database (Milvus) ---
    vector_db_host: str = "localhost"
    vector_db_port: int = 19530
    vector_db_type: str = "milvus"  # milvus | qdrant
    milvus_uri: str = ""  # explicit override; empty → derived from host/port
    milvus_collection: str = "hrbp_chunks"

    # --- Object Storage (MinIO) ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "hrbp-workbench"
    minio_secure: bool = False

    # --- Retrieval (hybrid RAG) ---
    rrf_k: int = 60  # RRF constant: score += 1 / (rrf_k + rank)
    dense_top_k: int = 20  # candidate count fetched from Milvus before fusion
    sparse_top_k: int = 20  # candidate count fetched from PostgreSQL before fusion

    # --- CORS ---
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Guardrails ---
    guardrail_pii_detection_enabled: bool = True
    guardrail_prompt_injection_enabled: bool = True
    # Factuality verification is currently a conservative heuristic rather than
    # a dedicated NLI/LLM verifier, so keep the flag explicit instead of claiming
    # deep factual validation.
    guardrail_factuality_check_enabled: bool = False
    guardrail_toxicity_detection_enabled: bool = True
    guardrail_confidence_threshold: float = 0.65

    # --- Rate Limiting ---
    rate_limit_tenant_per_minute: int = 60
    rate_limit_user_per_minute: int = 30

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend_url: str = "redis://localhost:6379/2"

    # --- Logging ---
    log_level: str = "info"  # debug | info | warn | error
    log_format: str = "json"  # json | text

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if self.jwt_secret == "change-me-in-production" or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be a non-default value of at least 32 characters in production")
            if not self.llm_api_key or self.llm_api_key == "change-me":
                raise ValueError("LLM_API_KEY must be configured in production")
            if not self.effective_embedding_api_key or not self.embedding_base_url:
                raise ValueError("EMBEDDING_API_KEY and EMBEDDING_BASE_URL must be configured in production")
        return self

    # --- Computed helpers ---
    @property
    def cors_origins_list(self) -> list[str]:
        """Split comma-separated CORS origins into a list."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_embedding_api_key(self) -> str:
        """Use llm_api_key as fallback if embedding_api_key is not set."""
        return self.embedding_api_key or self.llm_api_key

    @property
    def milvus_endpoint(self) -> str:
        """Resolve the Milvus connection URI (explicit override wins)."""
        return self.milvus_uri or f"http://{self.vector_db_host}:{self.vector_db_port}"


settings = Settings()  # Fails fast if required vars are missing
