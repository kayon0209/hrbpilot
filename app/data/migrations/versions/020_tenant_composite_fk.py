"""Tenant composite foreign keys for all child->parent business relations.

Revision ID: 020_tenant_composite_fk
Revises: 019_oauth_nonce_csrf

P0-05: a single-column FK on `id` cannot stop a writer that bypasses the
service layer from binding a child row in tenant A to a parent row in tenant B
— RLS and service checks are defense-in-depth, but the referential integrity
itself must be tenant-scoped.

This migration makes every child->parent relation a composite FK
(tenant_id, parent_id) -> (tenant_id, id). The parent side requires a
(tenant_id, id) unique constraint, which is added where missing.

Lock-time note: adding a UNIQUE constraint and an FK takes ACCESS EXCLUSIVE
locks per table and scans the table once. On the current schema (empty or
small tenant tables) this is fast; on a large production table it must run in
a maintenance window. The upgrade/downgrade/upgrade round-trip is validated on
the isolated, disposable PostgreSQL database only — never on the business DB.

The application-owner migration connection has no tenant setting. FK
validation scans the existing table and would otherwise evaluate the forced
tenant policy (same constraint that migration 016 worked around), so every
FORCE-RLS table is temporarily NO FORCE while its constraints are added and
re-FORCEd afterwards.
"""

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision: str = "020_tenant_composite_fk"
down_revision: str | None = "019_oauth_nonce_csrf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that carry FORCE ROW LEVEL SECURITY (migration 014) and therefore need
# the NO FORCE dance around constraint addition.
_FORCED_RLS = frozenset(
    {
        "agent_runs",
        "approval_requests",
        "case_events",
        "case_plans",
        "chat_sessions",
        "culture_contents",
        "data_sources",
        "document_chunks",
        "documents",
        "insight_reports",
        "interview_digests",
        "knowledge_feedback_candidates",
        "manager_org_scopes",
        "org_units",
        "tool_executions",
        "weekly_reports",
        "hr_cases",
    }
)

# Parent unique constraints to add: table -> constraint name.
_PARENT_UNIQUE: dict[str, str] = {
    "hr_cases": "uq_hr_cases_tenant_id",
    "case_plans": "uq_case_plans_tenant_id",
    "agent_runs": "uq_agent_runs_tenant_id",
    "approval_requests": "uq_approval_requests_tenant_id",
    "data_sources": "uq_data_sources_tenant_id",
    "users": "uq_users_tenant_id",
    "org_units": "uq_org_units_tenant_id",
    "async_tasks": "uq_async_tasks_tenant_id",
    "documents": "uq_documents_tenant_id",
    "knowledge_bases": "uq_knowledge_bases_tenant_id",
    # work_tasks already has uq_work_tasks_tenant_id from migration 017.
}

# Child composite FKs: (child_table, constraint_name, parent_table, child_col).
_CHILD_FKS: list[tuple[str, str, str, str]] = [
    ("agent_runs", "fk_agent_runs_tenant_case", "hr_cases", "case_id"),
    ("approval_requests", "fk_approval_requests_tenant_case", "hr_cases", "case_id"),
    ("approval_requests", "fk_approval_requests_tenant_plan", "case_plans", "plan_id"),
    ("approval_requests", "fk_approval_requests_tenant_approver", "users", "approver_id"),
    ("async_tasks", "fk_async_tasks_tenant_creator", "users", "created_by"),
    ("case_events", "fk_case_events_tenant_case", "hr_cases", "case_id"),
    ("case_plans", "fk_case_plans_tenant_case", "hr_cases", "case_id"),
    ("case_plans", "fk_case_plans_tenant_run", "agent_runs", "agent_run_id"),
    ("chat_sessions", "fk_chat_sessions_tenant_user", "users", "user_id"),
    ("culture_contents", "fk_culture_contents_tenant_creator", "users", "created_by"),
    ("documents", "fk_documents_tenant_kb", "knowledge_bases", "kb_id"),
    ("hr_cases", "fk_hr_cases_tenant_creator", "users", "created_by"),
    ("hr_cases", "fk_hr_cases_tenant_owner", "users", "owner_id"),
    ("insight_reports", "fk_insight_reports_tenant_task", "async_tasks", "task_id"),
    ("interview_digests", "fk_interview_digests_tenant_document", "documents", "document_id"),
    (
        "knowledge_feedback_candidates",
        "fk_knowledge_feedback_candidates_tenant_user",
        "users",
        "source_user_id",
    ),
    (
        "knowledge_feedback_candidates",
        "fk_knowledge_feedback_candidates_tenant_org",
        "org_units",
        "org_unit_id",
    ),
    ("manager_org_scopes", "fk_manager_org_scopes_tenant_user", "users", "manager_user_id"),
    ("manager_org_scopes", "fk_manager_org_scopes_tenant_org", "org_units", "org_unit_id"),
    ("org_units", "fk_org_units_tenant_parent", "org_units", "parent_id"),
    ("tool_executions", "fk_tool_executions_tenant_case", "hr_cases", "case_id"),
    ("tool_executions", "fk_tool_executions_tenant_approval", "approval_requests", "approval_id"),
    ("tool_executions", "fk_tool_executions_tenant_run", "agent_runs", "agent_run_id"),
    ("users", "fk_users_tenant_org", "org_units", "org_unit_id"),
    ("weekly_reports", "fk_weekly_reports_tenant_creator", "users", "created_by"),
    ("connector_event_log", "fk_connector_event_log_tenant_source", "data_sources", "source_id"),
    ("connector_sync_cursors", "fk_connector_sync_cursors_tenant_source", "data_sources", "source_id"),
    ("oauth_nonces", "fk_oauth_nonces_tenant_source", "data_sources", "source_id"),
    ("document_chunks", "fk_document_chunks_tenant_document", "documents", "document_id"),
]


# Old single-column FKs (child table, constraint name) being REPLACED by the
# composite FKs above. Dropping them keeps ``alembic check`` aligned with the
# models, which no longer declare the single-column form.
_OLD_FKS: list[tuple[str, str]] = [
    ("agent_runs", "agent_runs_case_id_fkey"),
    ("approval_requests", "approval_requests_case_id_fkey"),
    ("approval_requests", "approval_requests_plan_id_fkey"),
    ("approval_requests", "approval_requests_approver_id_fkey"),
    ("async_tasks", "fk_async_tasks_created_by"),
    ("case_events", "case_events_case_id_fkey"),
    ("case_plans", "case_plans_case_id_fkey"),
    ("case_plans", "case_plans_agent_run_id_fkey"),
    ("chat_sessions", "fk_chat_sessions_user_id"),
    ("culture_contents", "fk_culture_contents_created_by"),
    ("documents", "fk_documents_kb_id"),
    ("hr_cases", "hr_cases_created_by_fkey"),
    ("hr_cases", "hr_cases_owner_id_fkey"),
    ("insight_reports", "fk_insight_reports_task_id"),
    ("interview_digests", "fk_interview_digests_document_id"),
    ("knowledge_feedback_candidates", "fk_knowledge_feedback_candidates_source_user"),
    ("knowledge_feedback_candidates", "fk_knowledge_feedback_candidates_org_unit"),
    ("manager_org_scopes", "fk_manager_org_scopes_manager"),
    ("manager_org_scopes", "fk_manager_org_scopes_org_unit"),
    ("org_units", "fk_org_units_parent_id"),
    ("tool_executions", "tool_executions_case_id_fkey"),
    ("tool_executions", "tool_executions_approval_id_fkey"),
    ("tool_executions", "tool_executions_agent_run_id_fkey"),
    ("users", "fk_users_org_unit_id"),
    ("weekly_reports", "fk_weekly_reports_created_by"),
    ("connector_event_log", "connector_event_log_source_id_fkey"),
    ("connector_sync_cursors", "connector_sync_cursors_source_id_fkey"),
    ("oauth_nonces", "oauth_nonces_source_id_fkey"),
    ("document_chunks", "fk_document_chunks_document_id"),
]


def _no_force(table: str) -> None:
    if table in _FORCED_RLS:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")


def _re_force(table: str) -> None:
    if table in _FORCED_RLS:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


@contextmanager
def _rls_relaxed(tables: Iterable[str]) -> Iterator[None]:
    """Lift FORCE ROW LEVEL SECURITY on every table that actually has it.

    The hard-coded ``_FORCED_RLS`` set above is documentation of what migration
    014 turned on, but it is not authoritative: a parent table that gains FORCE
    RLS later (``knowledge_bases`` and ``async_tasks`` both have it) is still
    invisible to the FK validation scan when the migration connection — which
    carries no tenant setting — reads it.  That makes ADD CONSTRAINT fail with
    "violates foreign key constraint" even though the parent rows exist.

    Probing ``pg_class`` instead of trusting a hand-maintained list fixes that
    class of failure: every table that currently has FORCE on is relaxed, and
    only those are re-FORCEd afterwards.  Tables that do not exist yet (or do
    not have FORCE on) are skipped, so the same code works on a fresh database
    and on one mid-history.
    """
    bind = op.get_bind()
    relaxed: list[str] = []
    try:
        for table in sorted(set(tables)):
            forced = bind.execute(
                sa.text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE relname = :table AND relkind = 'r'"
                ),
                {"table": table},
            ).scalar()
            if forced:
                op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
                relaxed.append(table)
        yield
    finally:
        for table in relaxed:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _add_composite_fk(child: str, constraint: str, parent: str, column: str, *, ondelete: str | None = None) -> None:
    op.create_foreign_key(
        constraint,
        child,
        parent,
        ["tenant_id", column],
        ["tenant_id", "id"],
        ondelete=ondelete,
    )


def _touched_tables() -> set[str]:
    """Every table this revision reads or writes."""
    return (
        set(_PARENT_UNIQUE)
        | {child for child, _ in _OLD_FKS}
        | {child for child, _, _, _ in _CHILD_FKS}
        | {parent for _, _, parent, _ in _CHILD_FKS}
    )


def upgrade() -> None:
    # Phase 1: parent (tenant_id, id) unique constraints (work_tasks exists).
    # Phase 2: drop the replaced single-column FKs, then add composite child
    # FKs.  Both phases scan tables, so FORCE RLS is lifted for the whole block
    # and restored afterwards — see _rls_relaxed.
    with _rls_relaxed(_touched_tables()):
        for parent, constraint in _PARENT_UNIQUE.items():
            op.execute(f"ALTER TABLE {parent} ADD CONSTRAINT {constraint} UNIQUE (tenant_id, id)")

        for child, constraint in _OLD_FKS:
            op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {constraint}")
        for child, constraint, parent, column in _CHILD_FKS:
            _add_composite_fk(
                child,
                constraint,
                parent,
                column,
                ondelete="CASCADE" if constraint == "fk_document_chunks_tenant_document" else None,
            )


def downgrade() -> None:
    with _rls_relaxed(_touched_tables()):
        for child, _constraint, _parent, _column in _CHILD_FKS:
            op.drop_constraint(_constraint, child, type_="foreignkey")

        # ``020`` replaced, rather than supplemented, the historical
        # single-column foreign keys.  A downgrade must therefore recreate
        # every previous constraint before earlier revisions are asked to
        # remove their own schema.  In particular, ``016`` owns
        # ``fk_culture_contents_created_by`` and cannot downgrade if this
        # revision merely drops the composite replacement.
        #
        # The lists intentionally have the same order.  Keep the invariant
        # explicit so a future composite FK cannot silently lose its historic
        # downgrade counterpart.
        for (old_child, old_constraint), (child, composite_constraint, parent, column) in zip(
            _OLD_FKS, _CHILD_FKS, strict=True
        ):
            if old_child != child:
                raise RuntimeError(
                    f"020 tenant-FK migration lists are out of sync: old={old_child!r}, composite={child!r}"
                )
            op.create_foreign_key(
                old_constraint,
                child,
                parent,
                [column],
                ["id"],
                ondelete="CASCADE" if composite_constraint == "fk_document_chunks_tenant_document" else None,
            )
        for parent, constraint in _PARENT_UNIQUE.items():
            op.execute(f"ALTER TABLE {parent} DROP CONSTRAINT IF EXISTS {constraint}")
