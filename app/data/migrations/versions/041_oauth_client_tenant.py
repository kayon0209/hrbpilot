"""Add tenant ownership to OAuth clients.

Revision ID: 041_oauth_client_tenant
Revises: 040_approval_requester_binding

为什么需要这一列
----------------
AS 登录原先把租户钉死为 ``"default"``（``app/oauth/identity.py``），多租户部署要接
外部 Agent 时，非默认租户的用户根本走不完授权流程 —— 登录在错误的用户目录里找人。
解除它的正确挂点是**客户端**：授权请求必然带着 ``client_id``，客户端归属哪个租户，
登录就在哪个租户的用户目录（``users`` 受 RLS）里找人，签出的令牌也携带该租户。

安全边界
--------
这个租户只能来自**预注册配置**（运维声明的信任关系）。注册文档（DCR / CIMD）不得
自带租户 —— 让自助注册的匿名请求自选租户，等于把跨租户入口写在协议里。所以迁移
给全部存量行一个缺省值，而写入路径上只有预注册同步能落非缺省值。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "041_oauth_client_tenant"
down_revision: str | None = "040_approval_requester_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "oauth_clients"
# 与 ``app.data.models.oauth.DEFAULT_TENANT`` 逐字一致。刻意不 import 应用代码：
# 迁移必须自包含（仓库既有迁移无一 import app），迁移永远不该因应用模块的重构而失效。
_DEFAULT_TENANT = "default"


def upgrade() -> None:
    # server_default 让存量行（含迁移中的并发读）立即有确定取值；随后不撤销 ——
    # 这列 NOT NULL 且永远不该为空，留着 server_default 与模型的 default 一致。
    op.add_column(
        _TABLE,
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default=_DEFAULT_TENANT),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "tenant_id")
