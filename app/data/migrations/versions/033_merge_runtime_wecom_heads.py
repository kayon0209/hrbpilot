"""Merge governed-runtime and WeCom delivery migration heads.

Revision ID: 033_merge_runtime_wecom_heads
Revises: 032_in_app_notifications, 030_wecom_outbound_simulator
Create Date: 2026-09-06

This is an Alembic merge revision only. Both parent branches contain
independent schema changes, so the revision intentionally performs no DDL.
"""

from collections.abc import Sequence

revision: str = "033_merge_runtime_wecom_heads"
down_revision: tuple[str, str] = ("032_in_app_notifications", "030_wecom_outbound_simulator")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
