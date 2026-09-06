"""Mark placeholder auto-eval scores as stub.

Revision ID: 029_eval_result_is_stub
Revises: 028_event_route_default
Create Date: 2026-09-03

History (verified against git history):
  - 886c670 (2026-08-15) introduced auto-eval placeholder constants
    (0.7 / 0.5) that were written into ``eval_results``.
  - 653e913 (2026-08-19) replaced the constants with a real LLM judge, but a
    failed judge still recorded a fabricated 0.0.
  - c7b9462 (2026-08-27 22:05:19 +0800) fixed the semantics: a failed judge is
    now skipped entirely and never written.

Every row written before the c7b9462 commit timestamp was produced by
defective code (constants or fabricated 0.0), so it is conservatively marked
``is_stub = true`` and excluded from quality aggregation.  Rows written after
the fix are real judge scores.  The boundary is conservative by design: a
small number of genuine early judge scores (2026-08-19..27) may also be
flagged, which is preferable to letting placeholder values drive trends.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "029_eval_result_is_stub"
down_revision: str | None = "028_event_route_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Commit timestamp of c7b9462 (fix(eval): separate zero scores from skipped judges).
_STUB_BOUNDARY = "2026-08-27T22:05:19+08:00"


def upgrade() -> None:
    # FORCE RLS blocks the table owner inside the migration; drop it around the
    # data fix exactly like revisions 014/028 do.
    op.execute("ALTER TABLE eval_results NO FORCE ROW LEVEL SECURITY")
    op.add_column("eval_results", sa.Column("is_stub", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE eval_results SET is_stub = true WHERE created_at < '" + _STUB_BOUNDARY + "'")
    # Keep server_default=false: the ORM mirrors it (EvalResult.is_stub) so
    # raw SQL inserts also default to measured instead of violating NOT NULL.
    op.execute("ALTER TABLE eval_results FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_results NO FORCE ROW LEVEL SECURITY")
    op.drop_column("eval_results", "is_stub")
    op.execute("ALTER TABLE eval_results FORCE ROW LEVEL SECURITY")
