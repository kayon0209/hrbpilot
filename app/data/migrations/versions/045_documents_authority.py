"""Add documents.authority — what a document *is*, not just whether it parses.

Revision ID: 045_documents_authority
Revises: 044_documents_tenant_dedup

Why
---
"Can we ingest it" (the format parses) and "can it be cited as policy" (the
source is authoritative) are two different questions, and this knowledge base
only ever answered the first one. The concrete failure: a
《酒店工程部员工转正、晋级技能考核表》 — a form, with nothing to do with leave
policy — ranked **first** for "公司的年假是怎么规定的？" because it literally
contains 年 and 假. Nothing about it is malformed. Format filtering cannot
catch that class; only a declared source type can.

Without this column the system has no way to distinguish:

* 国务院令第514号 — binding national law, the statutory floor;
* an hrtools 员工手册模板 — a third-party sample whose annual-leave clause
  literally reads "每满1年可享受×天" (placeholders, no numbers);
* a hotel skills-assessment form — not a policy document at all.

All three were being served as "制度片段" with equal standing.

Why the value is declared, never inferred
-----------------------------------------
Guessing a source type from a filename ("looks like a template") is exactly the
kind of heuristic that silently mislabels. The default is ``unknown`` on
purpose: it makes "nobody declared this" visible in the data instead of
disguising it as a known value. Classification is applied by
``scripts/classify_kb_authority.py`` with an explicit mapping, and future
imports declare it up front via ``scripts/import_kb_docs.py --authority``.

This migration is schema-only — no data backfill. A data backfill would have to
run as a non-superuser under ``FORCE ROW LEVEL SECURITY``, where an unscoped
``UPDATE`` cannot see (or touch) rows belonging to tenants. Doing it from a
tenant-scoped session in a script is both correct and re-runnable.

Note on revision-id length
--------------------------
``alembic_version.version_num`` is ``varchar(32)``; keep ids at or under 32
characters (see 044 for the failure mode).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "045_documents_authority"
down_revision: str | None = "044_documents_tenant_dedup"
branch_labels: str | None = None
depends_on: str | None = None

#: Column-level default used only while the column is being added. It is dropped
#: right after so the live schema matches the model (which declares the default
#: in Python, not in DDL) — otherwise the schema-drift check reports a mismatch.
_TEMPORARY_SERVER_DEFAULT = "unknown"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "authority",
            sa.String(length=32),
            nullable=False,
            server_default=_TEMPORARY_SERVER_DEFAULT,
        ),
    )
    op.alter_column("documents", "authority", server_default=None)


def downgrade() -> None:
    op.drop_column("documents", "authority")
