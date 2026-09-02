"""Connector credential envelope encryption.

Level 2 of the certification ladder requires real credentials, and real
credentials must never be stored in plaintext. This module implements
tenant-scoped envelope encryption:

- A single master key (CONNECTOR_MASTER_KEY, base64 of 32 bytes) comes from
  the environment; production/staging MUST configure a real one.
- Per-tenant key derivation: SHA-256(master || tenant_id) → Fernet key.
  A leak of one tenant's ciphertext cannot be decrypted with another
  tenant's derived key even under the same master.
- ciphertext never leaves the service: DataSource views exclude it, and the
  decrypt path is only called by the sync engine at use time.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config.settings import settings
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

_MAX_PLAINTEXT_BYTES = 64 * 1024


def _master_bytes() -> bytes:
    raw = settings.connector_master_key
    try:
        key = base64.urlsafe_b64decode(raw)
        if len(key) != 32:
            raise ValueError("not 32 bytes")
        return key
    except Exception as exc:
        raise AppError(
            "连接器主密钥未正确配置（CONNECTOR_MASTER_KEY 需为 32 字节的 base64）",
            code="CONFIG_ERROR",
            status_code=500,
        ) from exc


def _tenant_fernet(tenant_id: str, master: bytes) -> Fernet:
    derived = hashlib.sha256(master + tenant_id.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_credential(tenant_id: str, plaintext: str) -> bytes:
    """Encrypt a connector credential under the tenant's derived key."""
    if not plaintext:
        raise AppError("凭据内容不能为空", code="VALIDATION_ERROR", status_code=400)
    payload = plaintext.encode("utf-8")
    if len(payload) > _MAX_PLAINTEXT_BYTES:
        raise AppError("凭据内容过大", code="VALIDATION_ERROR", status_code=400)
    return _tenant_fernet(tenant_id, _master_bytes()).encrypt(payload)


def decrypt_credential(tenant_id: str, ciphertext: bytes) -> str:
    """Decrypt a tenant credential. Only the sync engine calls this."""
    try:
        return _tenant_fernet(tenant_id, _master_bytes()).decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        # Wrong tenant, wrong master key, or tampered ciphertext.
        logger.warning("connector_credential_decrypt_failed", tenant_id=tenant_id)
        raise AppError("凭据解密失败（租户或主密钥不匹配）", code="CONFIG_ERROR", status_code=500) from exc


def rotate_master_key(tenant_id: str, ciphertext: bytes, new_master: bytes) -> bytes:
    """Re-encrypt one tenant's ciphertext under a new master (rotation path)."""
    # Decryption uses the current master; callers pass the new master for the
    # re-encryption step. MultiFernet is used so a rotation window can hold
    # both keys without a big-bang migration.
    derived_new = hashlib.sha256(new_master + tenant_id.encode("utf-8")).digest()
    plaintext = decrypt_credential(tenant_id, ciphertext)
    return Fernet(base64.urlsafe_b64encode(derived_new)).encrypt(plaintext.encode("utf-8"))


def decrypt_with_masters(tenant_id: str, ciphertext: bytes, masters: list[bytes]) -> str:
    """Decrypt trying multiple masters (rotation window)."""
    fernets = [
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(m + tenant_id.encode("utf-8")).digest()))
        for m in masters
    ]
    try:
        return MultiFernet(fernets).decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        logger.warning("connector_credential_decrypt_failed", tenant_id=tenant_id)
        raise AppError("凭据解密失败（租户或主密钥不匹配）", code="CONFIG_ERROR", status_code=500) from exc
