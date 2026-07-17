"""Initial schema — all core tables + RLS policies

Revision ID: 001_initial
Revises: None
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables with tenant_id that need RLS
RLS_TABLES = [
    "users",
    "knowledge_bases",
    "chat_sessions",
    "interview_digests",
    "insight_reports",
    "weekly_reports",
    "culture_contents",
    "async_tasks",
    "audit_logs",
    "eval_results",
]


def upgrade() -> None:
    # --- tenants (NO RLS — Admin can access all) ---
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(200), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="employee"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # --- knowledge_bases ---
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("scenario_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("chunk_strategy", sa.String(100), nullable=False, server_default="default"),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])

    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kb_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("s3_key", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="uploaded"),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])

    # --- chat_sessions ---
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scenario_id", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_foreign_key("fk_chat_sessions_user_id", "chat_sessions", "users", ["user_id"], ["id"])

    # --- chat_messages ---
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_foreign_key("fk_chat_messages_session_id", "chat_messages", "chat_sessions", ["session_id"], ["id"])

    # --- interview_digests ---
    op.create_table(
        "interview_digests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("demands_json", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(10), nullable=False),
        sa.Column("risk_signals_json", sa.Text(), nullable=False),
        sa.Column("action_items_json", sa.Text(), nullable=False),
        sa.Column("suggested_owner", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_interview_digests_tenant_id", "interview_digests", ["tenant_id"])
    op.create_foreign_key("fk_interview_digests_document_id", "interview_digests", "documents", ["document_id"], ["id"])

    # --- insight_reports ---
    op.create_table(
        "insight_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("clusters_json", sa.Text(), nullable=False),
        sa.Column("signals_json", sa.Text(), nullable=False),
        sa.Column("trends_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_insight_reports_tenant_id", "insight_reports", ["tenant_id"])

    # --- weekly_reports ---
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("period", sa.String(50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=False),
        sa.Column("risks_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("data_sources_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_weekly_reports_tenant_id", "weekly_reports", ["tenant_id"])

    # --- culture_contents ---
    op.create_table(
        "culture_contents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("keywords_json", sa.Text(), nullable=False),
        sa.Column("news_article", sa.Text(), nullable=False),
        sa.Column("group_notice", sa.Text(), nullable=False),
        sa.Column("employee_story", sa.Text(), nullable=False),
        sa.Column("event_copy", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_culture_contents_tenant_id", "culture_contents", ["tenant_id"])

    # --- async_tasks ---
    op.create_table(
        "async_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_async_tasks_tenant_id", "async_tasks", ["tenant_id"])

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scenario_id", sa.String(50), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("retrieved_docs_json", sa.Text(), nullable=True),
        sa.Column("llm_model_version", sa.String(100), nullable=True),
        sa.Column("guardrail_result_json", sa.Text(), nullable=True),
        sa.Column("eval_score", sa.Float(), nullable=True),
        sa.Column("response_latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_consumption", sa.Integer(), nullable=True),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])

    # --- eval_results ---
    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("scenario_id", sa.String(50), nullable=False),
        sa.Column("metric", sa.String(100), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_eval_results_tenant_id", "eval_results", ["tenant_id"])
    op.create_index("ix_eval_results_scenario_id", "eval_results", ["scenario_id"])

    # --- RLS policies ---
    # Enable RLS on all tenant-scoped tables
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Policy: users can only see rows from their own tenant
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id'))"
        )

    # Also add foreign key for insight_reports → async_tasks
    op.create_foreign_key(
        "fk_insight_reports_task_id", "insight_reports", "async_tasks", ["task_id"], ["id"]
    )


def downgrade() -> None:
    # Drop RLS policies first
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop all tables in reverse dependency order
    op.drop_table("eval_results")
    op.drop_table("audit_logs")
    op.drop_table("async_tasks")
    op.drop_table("culture_contents")
    op.drop_table("weekly_reports")
    op.drop_table("insight_reports")
    op.drop_table("interview_digests")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.drop_table("users")
    op.drop_table("tenants")
