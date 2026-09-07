"""Static contracts for the Wave 2 persistence boundary."""

from app.data.models.hr_case import ToolExecution
from app.data.models.notification import InAppNotification
from app.data.models.runtime import ExecutionGrant, OutboxMessage


def test_runtime_coordination_tables_are_tenant_scoped_and_do_not_store_raw_payloads() -> None:
    assert "tenant_id" in ExecutionGrant.__table__.c
    assert "tenant_id" in OutboxMessage.__table__.c
    assert "payload_ref" in OutboxMessage.__table__.c
    assert "payload_digest" in OutboxMessage.__table__.c
    assert "payload_json" not in OutboxMessage.__table__.c
    assert "prompt" not in OutboxMessage.__table__.c


def test_grant_and_outbox_constraints_are_present() -> None:
    grant_constraints = {constraint.name for constraint in ExecutionGrant.__table__.constraints}
    outbox_constraints = {constraint.name for constraint in OutboxMessage.__table__.constraints}
    assert "ck_execution_grants_consumption_count" in grant_constraints
    assert "uq_outbox_messages_tenant_dedupe" in outbox_constraints


def test_tool_execution_persists_grant_outbox_and_dispatch_fence() -> None:
    columns = ToolExecution.__table__.c
    assert "execution_grant_id" in columns
    assert "outbox_message_id" in columns
    assert "dispatch_lease_owner" in columns
    assert "dispatch_lease_token" in columns
    assert "dispatch_lease_expires_at" in columns

    foreign_keys = {constraint.name for constraint in ToolExecution.__table__.foreign_key_constraints}
    constraints = {constraint.name for constraint in ToolExecution.__table__.constraints}
    assert "uq_tool_executions_tenant_id" in constraints
    assert "fk_tool_executions_tenant_grant" in foreign_keys
    assert "fk_tool_executions_tenant_outbox" in foreign_keys


def test_in_app_notifications_are_tenant_scoped_idempotent_recipient_deliveries() -> None:
    columns = InAppNotification.__table__.c
    assert {
        "tenant_id",
        "recipient_user_id",
        "case_id",
        "tool_execution_id",
        "template",
        "delivery_key",
        "read_at",
    } <= set(columns.keys())
    constraints = {constraint.name for constraint in InAppNotification.__table__.constraints}
    foreign_keys = {constraint.name for constraint in InAppNotification.__table__.foreign_key_constraints}
    assert "uq_in_app_notifications_tenant_delivery" in constraints
    assert {
        "fk_in_app_notifications_tenant_recipient",
        "fk_in_app_notifications_tenant_case",
        "fk_in_app_notifications_tenant_execution",
    } <= foreign_keys
