"""HRBP AI Workbench — global error handler middleware.

Catches all AppError subclasses and returns consistent JSON.
Programming errors → log + generic 500.
"""

import structlog

from fastapi import Request
from fastapi.responses import JSONResponse

from app.shared.errors import AppError

logger = structlog.get_logger(__name__)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle operational errors — return structured JSON response."""
    request_id = getattr(request.state, "request_id", "unknown")

    if exc.status_code < 500:
        logger.warning(
            "operational_error",
            code=exc.code,
            status=exc.status_code,
            message=exc.message,
            request_id=request_id,
            path=str(request.url.path),
        )
    else:
        logger.error(
            "operational_error",
            code=exc.code,
            status=exc.status_code,
            message=exc.message,
            request_id=request_id,
            path=str(request.url.path),
        )

    response = {
        "code": exc.code,
        "status": exc.status_code,
        "message": exc.message,
        "request_id": request_id,
    }

    # Attach extra fields for specific errors
    if hasattr(exc, "errors") and exc.errors:
        response["errors"] = exc.errors
    if hasattr(exc, "required_role") and exc.required_role:
        response["required_role"] = exc.required_role
    if hasattr(exc, "guardrail_type") and exc.guardrail_type:
        response["guardrail_type"] = exc.guardrail_type
    if hasattr(exc, "pii_types") and exc.pii_types:
        response["pii_types"] = exc.pii_types

    return JSONResponse(status_code=exc.status_code, content=response)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle programming errors — log details, return generic 500."""
    request_id = getattr(request.state, "request_id", "unknown")

    # Special handling for LLM rate limit / service errors
    error_type = type(exc).__name__
    error_str = str(exc)

    if "RateLimitError" in error_type or "429" in error_str:
        logger.warning("llm_rate_limited", error=error_str, request_id=request_id)
        return JSONResponse(
            status_code=429,
            content={
                "code": "LLM_RATE_LIMITED",
                "status": 429,
                "message": "AI 模型调用额度不足或限流，请稍后重试或充值 API 额度。",
                "request_id": request_id,
            },
        )

    if "APIConnectionError" in error_type or "APITimeoutError" in error_type:
        logger.error("llm_connection_error", error=error_str, request_id=request_id)
        return JSONResponse(
            status_code=502,
            content={
                "code": "LLM_UNAVAILABLE",
                "status": 502,
                "message": "AI 模型服务暂时不可用，请稍后重试。",
                "request_id": request_id,
            },
        )

    logger.error(
        "unhandled_error",
        error=error_str,
        error_type=error_type,
        request_id=request_id,
        path=str(request.url.path),
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "status": 500,
            "message": "An unexpected error occurred. Please try again later.",
            "request_id": request_id,
        },
    )
