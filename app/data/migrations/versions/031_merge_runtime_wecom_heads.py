"""Merge the governed-runtime and WeCom delivery migration heads.

Revision ID: 031_merge_runtime_wecom_heads
Revises: 030_runtime_grants_outbox, 030_wecom_outbound_simulator
Create Date: 2026-09-07

This is an Alembic merge revision only. Both parent branches contain
independent schema changes, so the revision intentionally performs no DDL.
"""

from collections.abc import Sequence

revision: str = "031_merge_runtime_wecom_heads"
down_revision: tuple[str, str] = ("030_runtime_grants_outbox", "030_wecom_outbound_simulator")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
