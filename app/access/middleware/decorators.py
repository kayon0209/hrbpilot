"""HRBP AI Workbench — Auth and RBAC route decorators.

These are convenience decorators for route handlers. The AuthMiddleware and
RBACMiddleware handle global checks at the middleware layer; these decorators
provide documentation clarity and a redundant per-handler check.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from fastapi import Request

from app.shared.errors import AuthError, ForbiddenError

P = ParamSpec("P")
R = TypeVar("R")


def require_capability(
    capability: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Require one explicit capability; roles never inherit from each other."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            request: Request | None = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if not request:
                request = kwargs.get("request")  # type: ignore[assignment]

            if request:
                from app.access.middleware.rbac import ROLE_CAPABILITIES

                role = getattr(request.state, "user_role", "employee")
                if capability not in ROLE_CAPABILITIES.get(role, set()):
                    raise ForbiddenError(message="当前角色无权使用此功能", required_role=capability)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_auth(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Require a valid authenticated user (redundant with AuthMiddleware)."""

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        request: Request | None = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        if not request:
            request = kwargs.get("request")  # type: ignore[assignment]

        if request and not getattr(request.state, "user_id", None):
            raise AuthError("Authentication required")

        return await func(*args, **kwargs)

    return wrapper
