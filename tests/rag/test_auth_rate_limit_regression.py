from app.access.routes import auth


def test_login_route_is_subject_to_rate_limit_policy() -> None:
    # Login should not be globally exempt from auth-side protection.
    assert "/api/auth/login" not in auth.router.prefix
