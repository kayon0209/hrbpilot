"""HRBP AI Workbench — Auth and RBAC route decorators.

These are convenience decorators for route handlers.
The AuthMiddleware and RBACMiddleware handle global checks at the middleware layer.
These decorators provide:
  1. Documentation clarity (route-level role requirements visible in code)
  2. Optional redundant check for extra safety

Usage:
    @require_auth          # Just ensure user is logged in
    @require_role("hrbp")  # Ensure user has hrbp role or above
"""

from functools import wraps

from fastapi import Request

from app.shared.errors import AuthError, ForbiddenError


# Role hierarchy for "or above" checks
ROLE_HIERARCHY = {
    "employee": 0,
    "hrbp": 1,
    "hr_manager": 2,
    "admin": 3,
}


def require_auth(func):
    """Decorator that requires a valid authenticated user.

    The AuthMiddleware already handles this globally, but this decorator
    provides a redundant check and makes auth requirements explicit in code.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract request from args/kwargs
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        if not request:
            request = kwargs.get("request")

        # Middleware already set user context; redundant check for safety
        if request and not getattr(request.state, "user_id", None):
            raise AuthError("Authentication required")

        return await func(*args, **kwargs)
    return wrapper


def require_role(min_role: str):
    """Decorator that requires a minimum role level.

    The RBACMiddleware handles global route-level checks.
    This decorator provides an explicit per-handler check.

    Args:
        min_role: Minimum role required (employee, hrbp, hr_manager, admin)

    Uses role hierarchy: hrbp requirement also allows hr_manager and admin.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if not request:
                request = kwargs.get("request")

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
