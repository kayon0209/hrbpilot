"""HRBP AI Workbench — Development mock user store.

When PostgreSQL is unavailable (dev mode), the auth system falls back to
these mock users so the frontend can still test all features.
"""

import hashlib
from dataclasses import dataclass


@dataclass
class MockUser:
    id: str
    email: str
    name: str
    role: str
    tenant_id: str
    hashed_password: str  # bcrypt-style hash


def _hash_password(password: str) -> str:
    """Simple hash for dev mode (NOT for production — uses SHA256)."""
    return hashlib.sha256(password.encode()).hexdigest()


# Pre-configured dev users for each role
_MOCK_USERS: dict[str, MockUser] = {
    "employee@hrbp.com": MockUser(
        id="usr-emp-001",
        email="employee@hrbp.com",
        name="张三（员工）",
        role="employee",
        tenant_id="tenant-001",
        hashed_password=_hash_password("123456"),
    ),
    "hrbp@hrbp.com": MockUser(
        id="usr-hrbp-001",
        email="hrbp@hrbp.com",
        name="李四（HRBP）",
        role="hrbp",
        tenant_id="tenant-001",
        hashed_password=_hash_password("123456"),
    ),
    "manager@hrbp.com": MockUser(
        id="usr-mgr-001",
        email="manager@hrbp.com",
        name="王五（HR经理）",
        role="hr_manager",
        tenant_id="tenant-001",
        hashed_password=_hash_password("123456"),
    ),
    "admin@hrbp.com": MockUser(
        id="usr-adm-001",
        email="admin@hrbp.com",
        name="赵六（管理员）",
        role="admin",
        tenant_id="tenant-001",
        hashed_password=_hash_password("123456"),
    ),
}


def get_mock_user_by_email(email: str) -> MockUser | None:
    """Look up a mock user by email."""
    return _MOCK_USERS.get(email.lower())


def get_mock_user_by_id(user_id: str) -> MockUser | None:
    """Look up a mock user by ID."""
    for user in _MOCK_USERS.values():
        if user.id == user_id:
            return user
    return None


def verify_mock_password(password: str, hashed: str) -> bool:
    """Verify password against dev hash."""
    return _hash_password(password) == hashed
