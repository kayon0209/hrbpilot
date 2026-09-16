"""Scope document deduplication to the owning tenant.

Revision ID: 044_documents_tenant_dedup
Revises: 043_agent_tasks

Why
---
``003_document_integrity`` built the dedup index on ``(kb_id, content_sha256)``
**without** ``tenant_id``. That makes the uniqueness constraint global while the
data is tenant-scoped — two tenants storing the same bytes under the same
``kb_id`` collide with each other.

The failure is asymmetric and easy to miss:

* the *second* tenant to upload gets an ``IntegrityError``, raised by a row it
  is not allowed to see — RLS hides the conflicting row, so the error looks
  inexplicable from that tenant's perspective;
* the *first* tenant is unaffected, so single-tenant testing never shows it.

Nothing legitimately depends on cross-tenant dedup: two tenants holding the same
public regulation text are two independent rows. Both dedup lookups in the
codebase (``scripts/import_kb_docs.py`` and ``app/access/routes/kb.py``) run
inside a tenant-scoped session, so their effective semantics are already
per-tenant — this migration makes the *constraint* agree with them.

The fix direction matters: adding ``tenant_id`` makes the key **less**
restrictive, so it cannot fail on existing data. No backfill is required.

Note on revision-id length
--------------------------
``alembic_version.version_num`` is ``varchar(32)``. A longer revision id fails
at the very end of ``upgrade`` with ``StringDataRightTruncationError`` from the
``UPDATE alembic_version`` statement — after the DDL has already run, inside the
same transaction. Keep ids <= 32 characters (the longest existing id is exactly
32). This migration is 26.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "044_documents_tenant_dedup"
down_revision: str | None = "043_agent_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_documents_kb_content_sha256_nonempty"
_NONEMPTY = sa.text("content_sha256 <> ''")


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="documents")
    op.create_index(
        _INDEX,
        "documents",
        ["tenant_id", "kb_id", "content_sha256"],
        unique=True,
        postgresql_where=_NONEMPTY,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="documents")
    op.create_index(
        _INDEX,
        "documents",
        ["kb_id", "content_sha256"],
        unique=True,
        postgresql_where=_NONEMPTY,
    )
