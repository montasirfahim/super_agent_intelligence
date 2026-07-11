"""Smoke tests for SQLModel schema registration (no DB connection required)."""
from app.models import (
    Agent,
    AgentProviderAssignment,
    Alert,
    AuditLog,
    Division,
    Provider,
    ProviderWallet,
    RiskAnalyst,
    TerritoryOffice,
    Ticket,
    TransactionStream,
)
from app.models.schema import SQLModel


def test_all_core_tables_are_registered():
    table_names = set(SQLModel.metadata.tables.keys())
    expected = {
        "provider",
        "division",
        "riskanalyst",
        "territoryoffice",
        "agent",
        "agentproviderassignment",
        "providerwallet",
        "transactionstream",
        "alert",
        "ticket",
        "auditlog",
    }
    missing = expected - table_names
    assert not missing, f"Missing tables in metadata: {missing}"


def test_agent_has_required_columns():
    cols = {c.name for c in Agent.__table__.columns}
    required = {"agent_id", "shop_name", "area", "district", "shared_physical_cash", "status"}
    assert required.issubset(cols), f"Agent missing columns: {required - cols}"


def test_transaction_has_customer_id_hash():
    cols = {c.name for c in TransactionStream.__table__.columns}
    assert "customer_id_hash" in cols, "TransactionStream must anonymize via customer_id_hash"


def test_alert_carries_bengali_and_evidence_fields():
    cols = {c.name for c in Alert.__table__.columns}
    required = {"alert_id", "message_bn", "confidence_score", "master_evidence_json"}
    assert required.issubset(cols), f"Alert missing: {required - cols}"


def test_ticket_has_state_machine_columns():
    cols = {c.name for c in Ticket.__table__.columns}
    required = {"ticket_id", "status", "current_owner_role", "evidence_json"}
    assert required.issubset(cols), f"Ticket missing: {required - cols}"


def test_auditlog_records_state_transitions():
    cols = {c.name for c in AuditLog.__table__.columns}
    required = {"log_id", "ticket_id", "action_taken", "performed_by_role", "timestamp"}
    assert required.issubset(cols), f"AuditLog missing: {required - cols}"