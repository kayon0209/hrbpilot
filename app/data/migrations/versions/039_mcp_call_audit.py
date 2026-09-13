"""MCP call audit — persistent record of every external Agent tool call.

Revision ID: 039_mcp_call_audit
Revises: 038_oauth_authorization_server

方案 §4.1 第 4 条要求外部 Agent 的调用可审计，且审计的维度要包含
tenant / user / client / installation / credential。``audit_logs`` 表承载的是
工作台场景（scenario、input/output 摘要、token 消耗），回答不了"是哪个客户端
打的" —— 所以这里单独一张表。

与 ``oauth_*`` 四张表相反，本表**启用 RLS**
--------------------------------------------
那四张表的每一次查询都发生在"还不知道租户是谁"的时刻，开 RLS 会让它们恒返回
零行。本表不同：写入时主体已经建好、租户已经确定（否则这次调用早在传输层就
401 了）。因此 RLS 在这里既有效又必要 —— 审计行里有工具名、对象引用与参数
摘要，跨租户可见是一个真实的泄漏面。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039_mcp_call_audit"
down_revision: str | None = "038_oauth_authorization_server"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "mcp_call_audits"


def _grant_to_app_role(table: str) -> None:
    """让非超级用户的应用角色能用这张表。

    迁移在容器里以数据库管理员身份运行，而 API 与 Worker 刻意以 ``hrbp`` 角色
    连接 —— 没有这一步，表建好了但每个请求都卡在 permission denied。
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrbp') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO hrbp';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=True),
        sa.Column("credential_id", sa.String(length=64), nullable=True),
        sa.Column("auth_method", sa.String(length=20), nullable=False),
        sa.Column("tool", sa.String(length=100), nullable=False),
        sa.Column("outcome_code", sa.String(length=50), nullable=False),
        sa.Column("deny_reason", sa.String(length=50), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("params_digest", sa.String(length=64), nullable=True),
        sa.Column("object_ref", sa.String(length=100), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_call_audits_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_mcp_call_audits_created_at", _TABLE, ["created_at"])
    op.create_index("ix_mcp_call_audits_user_id", _TABLE, ["user_id"])
    op.create_index("ix_mcp_call_audits_client_id", _TABLE, ["client_id"])
    op.create_index("ix_mcp_call_audits_installation_id", _TABLE, ["installation_id"])
    op.create_index("ix_mcp_call_audits_credential_id", _TABLE, ["credential_id"])
    op.create_index("ix_mcp_call_audits_tool", _TABLE, ["tool"])
    op.create_index("ix_mcp_call_audits_approval_id", _TABLE, ["approval_id"])
    op.create_index("ix_mcp_call_audits_trace_id", _TABLE, ["trace_id"])

    # 审计行里有工具名、对象引用与参数摘要 —— 跨租户可见是真实泄漏面，
    # 不靠应用层自觉。这里租户已确定，所以 RLS 是有效的。
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation_{_TABLE} ON {_TABLE} USING (tenant_id = current_setting('app.tenant_id'))"
    )
    # 与其它业务表一致：连表owner也受策略约束，避免"owner 绕过"成为事实后门。
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")

    _grant_to_app_role(_TABLE)


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{_TABLE} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(_TABLE)
