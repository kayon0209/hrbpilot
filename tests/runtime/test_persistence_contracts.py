"""Static contracts for the Wave 2 persistence boundary."""

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
