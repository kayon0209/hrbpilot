"""HRBP AI Workbench — FastAPI application entry point.

Middleware chain (in order):
  Request → RequestID → CORS → TenantContext → Auth → RBAC → Handler → ErrorHandler → Response

All routes and middleware are registered here.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.access.middleware.auth import AuthMiddleware
from app.access.middleware.cors import add_cors_middleware
from app.access.middleware.rate_limit import RateLimitMiddleware
from app.access.middleware.rbac import RBACMiddleware
from app.access.middleware.request_id import RequestIDMiddleware
from app.access.middleware.security_headers import SecurityHeadersMiddleware
from app.access.middleware.tenant import TenantContextMiddleware
from app.access.routes.admin_users import router as admin_users_router
from app.access.routes.audit import router as audit_router
from app.access.routes.auth import router as auth_router
from app.access.routes.connector_webhooks import router as connector_webhooks_router
from app.access.routes.culture_content import router as culture_router
from app.access.routes.data_source import router as data_source_router
from app.access.routes.employee_request import router as employee_request_router
from app.access.routes.eval import router as eval_metrics_router
from app.access.routes.health import router as health_router
from app.access.routes.hr_case import router as hr_case_router
from app.access.routes.interview_digest import router as interview_router
from app.access.routes.kb import router as kb_router
from app.access.routes.knowledge_feedback import router as knowledge_feedback_router
from app.access.routes.policy_qa import router as policy_qa_router
from app.access.routes.settings import router as settings_router
from app.access.routes.voice_insight import router as voice_router
from app.access.routes.weekly_report import router as weekly_router
from app.access.routes.work_summary import router as work_summary_router
from app.config.settings import settings
from app.shared.error_handler import app_error_handler, unhandled_error_handler
from app.shared.errors import AppError
from app.shared.logger import get_logger, setup_logging
from app.shared.shutdown import shutdown

logger = get_logger(__name__)

setup_logging()


async def _ensure_infrastructure() -> None:
    try:
        from app.rag.storage.milvus import MilvusStore

        await MilvusStore().ensure_collection_async()
    except Exception as e:
        logger.warning("milvus_not_ready_at_startup", error=str(e))
    try:
        from app.rag.storage.object_store import ObjectStore

        await ObjectStore().ensure_bucket_async()
    except Exception as e:
        logger.warning("minio_not_ready_at_startup", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ensure_infrastructure()
    try:
        yield
    finally:
        await shutdown("lifespan", timeout=30.0)


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)
    add_cors_middleware(app)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(RateLimitMiddleware)
    # Order matters: middleware added LAST runs FIRST. Auth must verify the JWT
    # and export scope["auth"] BEFORE RBAC (inner) can gate on the role —
    # BaseHTTPMiddleware layers each get their own request.state, and scope
    # writes from an inner layer are only visible to outer layers AFTER the
    # response returns, which is too late for an authorization decision.
    app.add_middleware(RBACMiddleware)
    app.add_middleware(AuthMiddleware)

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router)
    app.include_router(hr_case_router)
    app.include_router(policy_qa_router)
    app.include_router(interview_router)
    app.include_router(voice_router)
    app.include_router(weekly_router)
    app.include_router(culture_router)
    app.include_router(kb_router)
    app.include_router(settings_router)
    app.include_router(eval_metrics_router)
    app.include_router(work_summary_router)
    app.include_router(knowledge_feedback_router)
    app.include_router(employee_request_router)
    app.include_router(data_source_router)
    app.include_router(connector_webhooks_router)
    app.include_router(audit_router)
    app.include_router(admin_users_router)

    logger.info("app_created", app=settings.app_name, env=settings.app_env)
    return app


async def _validation_error_handler(request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    errors = [
        {
            "field": ".".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "status": 422,
            "message": "Request validation failed",
            "request_id": request_id,
            "errors": errors,
        },
    )


app = create_app()
