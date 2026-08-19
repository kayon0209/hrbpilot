"""HRBP AI Workbench — Development mock user store.

When PostgreSQL is unavailable (dev mode), the auth system falls back to
these mock users so the frontend can still test all features.

Emails use the ``hrbpilot.local`` domain so they match the convention
documented in the audit guide.  The default dev password (``Hainan.8848``)
is documented in the README only and is never returned by any API endpoint.
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


# Pre-configured dev users for each role (emails aligned with audit guide)
_MOCK_USERS: dict[str, MockUser] = {
    "employee@hrbpilot.local": MockUser(
        id="usr-emp-001",
        email="employee@hrbpilot.local",
        name="张三（员工）",
        role="employee",
        tenant_id="tenant-001",
        hashed_password=_hash_password("Hainan.8848"),
    ),
    "hrbp@hrbpilot.local": MockUser(
        id="usr-hrbp-001",
        email="hrbp@hrbpilot.local",
        name="李四（HRBP）",
        role="hrbp",
        tenant_id="tenant-001",
        hashed_password=_hash_password("Hainan.8848"),
    ),
    "manager@hrbpilot.local": MockUser(
        id="usr-mgr-001",
        email="manager@hrbpilot.local",
        name="王五（HR经理）",
        role="hr_manager",
        tenant_id="tenant-001",
        hashed_password=_hash_password("Hainan.8848"),
    ),
    "admin@hrbpilot.local": MockUser(
        id="usr-adm-001",
        email="admin@hrbpilot.local",
        name="赵六（管理员）",
        role="admin",
        tenant_id="tenant-001",
        hashed_password=_hash_password("Hainan.8848"),
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
