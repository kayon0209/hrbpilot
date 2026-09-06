"""Envelope encryption for connector credentials — security contract tests."""

import base64

import pytest

from app.connectors.credentials import (
    decrypt_credential,
    decrypt_with_masters,
    encrypt_credential,
    rotate_master_key,
)
from app.shared.errors import AppError


def test_roundtrip_under_tenant_key() -> None:
    ciphertext = encrypt_credential("tenant-a", "app-secret-token")
    assert isinstance(ciphertext, bytes)
    assert b"app-secret-token" not in ciphertext
    assert decrypt_credential("tenant-a", ciphertext) == "app-secret-token"


def test_ciphertext_is_tenant_scoped() -> None:
    """Tenant B's key must not decrypt tenant A's ciphertext."""
    ciphertext = encrypt_credential("tenant-a", "app-secret-token")
    with pytest.raises(AppError) as exc_info:
        decrypt_credential("tenant-b", ciphertext)
    assert "解密失败" in str(exc_info.value)


def test_plaintext_never_appears_in_ciphertext() -> None:
    secret = "feishu-app-secret-9f8e7d6c"
    ciphertext = encrypt_credential("tenant-001", secret)
    assert secret.encode() not in ciphertext
    assert base64.urlsafe_b64decode(ciphertext).find(secret.encode()) == -1


def test_empty_credential_rejected() -> None:
    with pytest.raises(AppError):
        encrypt_credential("tenant-a", "")


def test_rotation_reencrypts_under_new_master() -> None:
    master_old = base64.urlsafe_b64decode("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    master_new = bytes(range(32))
    ciphertext = encrypt_credential("tenant-a", "rotating-secret")

    rotated = rotate_master_key("tenant-a", ciphertext, master_new)
    # During the rotation window both masters must work.
    assert decrypt_with_masters("tenant-a", ciphertext, [master_old, master_new]) == "rotating-secret"
    assert decrypt_with_masters("tenant-a", rotated, [master_old, master_new]) == "rotating-secret"
