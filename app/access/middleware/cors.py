"""HRBP AI Workbench — CORS middleware.

Explicit origins only, never '*' in production.
"""

from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings


def add_cors_middleware(app) -> None:
    """Add CORS middleware with explicit origins from settings."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Tenant-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
