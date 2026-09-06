"""Persist a separate encrypted configuration bundle for WeCom callbacks.

Revision ID: 029_wecom_callback_config
Revises: 028_event_route_default
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "029_wecom_callback_config"
down_revision: str | None = "028_event_route_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "data_sources",
        sa.Column("wecom_callback_config_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.create_check_constraint(
        "ck_data_sources_wecom_callback_config",
        "data_sources",
        "wecom_callback_config_encrypted IS NULL OR platform = 'wecom'",
    )
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_data_sources_wecom_callback_config", "data_sources", type_="check")
    op.drop_column("data_sources", "wecom_callback_config_encrypted")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")
