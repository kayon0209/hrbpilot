"""Chunk page attribution: trace a retrieved chunk back to its source page.

Revision ID: 037_chunk_page_number
Revises: 036_material_search_and_batch

Adds ``document_chunks.page_number`` so a cited chunk can be traced back to the
page it came from — for HR policy answers, "which page says this" is part of
the answer's credibility, not a nice-to-have.

Purely additive and backward compatible: the column is nullable and existing
rows simply have no page. Formats without a page concept (txt/docx) also leave
it NULL, so nothing is invented.

Revision ID: 037_chunk_page_number
Revises: 036_material_search_and_batch
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037_chunk_page_number"
down_revision: str | None = "036_material_search_and_batch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("page_number", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "page_number")
