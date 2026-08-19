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

# Role hierarchy for "or above" checks
ROLE_HIERARCHY: dict[str, int] = {
    "employee": 0,
    "hrbp": 1,
    "hr_manager": 2,
    "admin": 3,
}

P = ParamSpec("P")
R = TypeVar("R")


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


def require_role(
    min_role: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Require a minimum role level (redundant with RBACMiddleware)."""

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
                user_role = getattr(request.state, "user_role", "employee")
                min_level = ROLE_HIERARCHY.get(min_role, 0)
                user_level = ROLE_HIERARCHY.get(user_role, 0)
                if user_level < min_level:
                    raise ForbiddenError(
                        message=f"需要 '{min_role}' 或以上角色，当前角色: '{user_role}'",
                        required_role=min_role,
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
