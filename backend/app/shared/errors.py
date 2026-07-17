"""HRBP AI Workbench — typed error hierarchy.

Every error is typed, logged, and returns a consistent JSON format.
Never throw generic Error('something') — use the appropriate subclass.
"""

from __future__ import annotations


class AppError(Exception):
    """Base error for all application errors."""

    def __init__(
        self,
        message: str,
        code: str,
        status_code: int,
        is_operational: bool = True,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.is_operational = is_operational

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status_code,
        }


# ---- 4xx Client Errors ----

class NotFoundError(AppError):
    """Resource not found (404)."""

    def __init__(self, resource: str, id: str) -> None:
        super().__init__(
            message=f"{resource} not found: {id}",
            code="NOT_FOUND",
            status_code=404,
        )


class ValidationError(AppError):
    """Input validation failed (422)."""

    def __init__(self, message: str = "Validation failed", errors: list[dict] | None = None) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
        )
        self.errors = errors or []


class AuthError(AppError):
    """Authentication failed (401)."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(
            message=message,
            code="AUTH_ERROR",
            status_code=401,
        )


class ForbiddenError(AppError):
    """Authorization failed — user lacks permission (403)."""

    def __init__(self, message: str = "Insufficient permissions", required_role: str | None = None) -> None:
        super().__init__(
            message=message,
            code="FORBIDDEN",
            status_code=403,
        )
        self.required_role = required_role


class ConflictError(AppError):
    """Resource conflict (409)."""

    def __init__(self, message: str, resource: str | None = None) -> None:
        super().__init__(
            message=message,
            code="CONFLICT",
            status_code=409,
        )
        self.resource = resource


class RateLimitError(AppError):
    """Rate limit exceeded (429)."""

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            status_code=429,
        )


# ---- Guardrail Errors ----

class GuardrailError(AppError):
    """Guardrail interception — input/output blocked."""

    def __init__(
        self,
        message: str,
        code: str = "GUARDRAIL_BLOCKED",
        status_code: int = 400,
        guardrail_type: str | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
        )
        self.guardrail_type = guardrail_type


class PromptInjectionError(GuardrailError):
    """Prompt injection detected — reject immediately."""

    def __init__(self, message: str = "Potential prompt injection detected") -> None:
        super().__init__(
            message=message,
            code="PROMPT_INJECTION_BLOCKED",
            status_code=400,
            guardrail_type="prompt_injection",
        )


class PIIError(GuardrailError):
    """PII detected — will be desensitized, not blocked."""

    def __init__(self, message: str = "PII detected and desensitized", pii_types: list[str] | None = None) -> None:
        super().__init__(
            message=message,
            code="PII_DETECTED",
            status_code=200,  # Not blocked, just flagged
            guardrail_type="pii_detection",
        )
        self.pii_types = pii_types or []


class ToxicityError(GuardrailError):
    """Toxic content detected — replaced with safe reply."""

    def __init__(self, message: str = "Toxic content detected and replaced") -> None:
        super().__init__(
            message=message,
            code="TOXICITY_BLOCKED",
            status_code=200,  # Not blocked, replaced
            guardrail_type="toxicity",
        )


# ---- 5xx Server Errors ----

class LLMError(AppError):
    """LLM service error (502)."""

    def __init__(self, message: str = "LLM service unavailable", provider: str | None = None) -> None:
        super().__init__(
            message=message,
            code="LLM_ERROR",
            status_code=502,
        )
        self.provider = provider


class ExternalServiceError(AppError):
    """External service (vector DB, storage, etc.) unavailable (503)."""

    def __init__(self, service: str, message: str | None = None) -> None:
        super().__init__(
            message=message or f"{service} service unavailable",
            code="EXTERNAL_SERVICE_ERROR",
            status_code=503,
        )
        self.service = service


class DatabaseError(AppError):
    """Database operation failed (500)."""

    def __init__(self, message: str = "Database operation failed") -> None:
        super().__init__(
            message=message,
            code="DATABASE_ERROR",
            status_code=500,
        )
