"""Merge the governed-runtime integration heads after codex branch landing.

Revision ID: 034_merge_governed_runtime_heads
Revises: 031_merge_runtime_wecom_heads, 033_merge_runtime_wecom_heads
Create Date: 2026-09-07

This is an Alembic merge revision only.  It reconciles the migration head
introduced by the governed-agent-runtime codex branch (``033_merge_runtime_wecom_heads``,
which carries the tool-dispatch fence and in-app-notification tables) with the
head already present on main (``031_merge_runtime_wecom_heads``).  Both parent
chains contain independent schema changes, so the revision performs no DDL.
"""

from collections.abc import Sequence

revision: str = "034_merge_governed_runtime_heads"
down_revision: tuple[str, str] = ("031_merge_runtime_wecom_heads", "033_merge_runtime_wecom_heads")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
