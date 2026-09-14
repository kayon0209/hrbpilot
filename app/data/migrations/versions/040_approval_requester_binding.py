"""Bind approvals to the requesting client and installation.

Revision ID: 040_approval_requester_binding
Revises: 039_mcp_call_audit

方案 §WP7 要求审批绑定 tenant / user / client / installation。前两者已有
(``tenant_id`` / ``requested_by``)，这里补上后两者。

为什么这不是审计字段
--------------------
撤销授权时，用户与运维期望的是"这个 Agent 从此不能再动我的东西"。若审批只记
租户与用户，一条已经躺在队列里的写操作在审批通过时照样会执行 —— 撤销挡住了新
调用，却漏掉了最要紧的那一个。所以绑定必须能在**决定审批之前**被查出来。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "040_approval_requester_binding"
down_revision: str | None = "039_mcp_call_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "approval_requests"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("requester_user_id", sa.String(length=36), nullable=True))
    op.add_column(_TABLE, sa.Column("client_id", sa.String(length=255), nullable=True))
    op.add_column(_TABLE, sa.Column("installation_id", sa.String(length=36), nullable=True))
    # 撤销校验要按 installation 反查"有没有待审批"，没有索引时那会是一次全表扫描。
    op.create_index("ix_approval_requests_installation_id", _TABLE, ["installation_id"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_installation_id", table_name=_TABLE)
    op.drop_column(_TABLE, "installation_id")
    op.drop_column(_TABLE, "client_id")
    op.drop_column(_TABLE, "requester_user_id")
