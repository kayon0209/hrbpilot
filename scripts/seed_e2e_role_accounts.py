"""Seed disposable, real-DB accounts for local Playwright acceptance.

The script deliberately has no default password and is only intended for an
isolated test database. It creates a tenant-scoped set of bcrypt accounts
without printing their password, so callers can inject the credentials into
the Playwright process rather than writing them to ``.env``.
"""

import asyncio
import hashlib
import os
import re
import sys
from uuid import uuid4

import bcrypt

from app.config.settings import settings
from app.data.database import make_tenant_session
from app.data.models.knowledge_base import Document, DocumentChunk, KnowledgeBase
from app.data.models.user import User

_ROLES = ("employee", "hrbp", "hr_manager", "admin")
_POLICY_TEXT = "请假超过三天须经 HR 总监审批，并在 OA 系统提交申请。"


def _require_run_id() -> str:
    run_id = os.environ.get("E2E_RUN_ID", "")
    if not re.fullmatch(r"[a-z0-9]{6,16}", run_id):
        raise ValueError("E2E_RUN_ID must be 6-16 lowercase letters or digits")
    return run_id


def _require_password() -> str:
    password = os.environ.get("E2E_SEED_PASSWORD", "")
    if len(password) < 16:
        raise ValueError("E2E_SEED_PASSWORD must contain at least 16 characters")
    return password


async def seed() -> None:
    run_id = _require_run_id()
    password = _require_password()
    tenant_id = f"e2e-{run_id}"
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    knowledge_base_id = str(uuid4())
    document_id = str(uuid4())
    content_sha256 = hashlib.sha256(_POLICY_TEXT.encode("utf-8")).hexdigest()

    # The local development configuration may enable SQLAlchemy echo. Account
    # seeding must never emit even a password hash through bound SQL logging.
    settings.app_debug = False
    session = await make_tenant_session(tenant_id)
    try:
        knowledge_base = KnowledgeBase(
            id=knowledge_base_id,
            tenant_id=tenant_id,
            scenario_id="policy_qa",
            name="E2E 请假制度库",
            chunk_strategy="fixed_512",
            chunk_size=512,
            status="active",
        )
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            id=document_id,
            tenant_id=tenant_id,
            kb_id=knowledge_base_id,
            filename="e2e-leave-policy.txt",
            s3_key=f"e2e/{run_id}/leave-policy.txt",
            file_type="txt",
            size_bytes=len(_POLICY_TEXT.encode("utf-8")),
            content_sha256=content_sha256,
            status="indexed",
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                id=str(uuid4()),
                tenant_id=tenant_id,
                kb_id=knowledge_base_id,
                document_id=document_id,
                chunk_index=0,
                content=_POLICY_TEXT,
                keyword_text="请假 超过 三天 HR 总监 审批 OA 系统 申请",
                section="请假审批",
                start_char=0,
                end_char=len(_POLICY_TEXT),
                content_sha256=content_sha256,
                embedding_model="e2e-deterministic",
                status="active",
            )
        )
        session.add_all(
            [
                User(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    name=f"E2E {role}",
                    email=f"e2e-{role}-{run_id}@hrbpilot.test",
                    hashed_password=password_hash,
                    role=role,
                )
                for role in _ROLES
            ]
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

    print(f"Seeded disposable E2E accounts and policy evidence for tenant {tenant_id}: {', '.join(_ROLES)}")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except ValueError as exc:
        print(f"E2E account seed refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
