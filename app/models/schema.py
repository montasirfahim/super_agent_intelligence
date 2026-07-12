from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class TransactionType(str, Enum):
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"


class AlertType(str, Enum):
    LIQUIDITY_SHORTAGE = "LIQUIDITY_SHORTAGE"
    BEHAVIORAL_ANOMALY = "BEHAVIORAL_ANOMALY"


class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OwnerRole(str, Enum):
    AGENT = "AGENT"
    FIELD_OFFICER = "FIELD_OFFICER"
    AREA_MANAGER = "AREA_MANAGER"
    CENTRAL_OPS = "CENTRAL_OPS"


class TicketStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"


class AuditAction(str, Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    ESCALATE = "ESCALATE"
    ADD_NOTE = "ADD_NOTE"
    CLOSE = "CLOSE"


class Provider(SQLModel, table=True):
    id: str = Field(primary_key=True)
    provider_name: str

    risk_analysts: List["RiskAnalyst"] = Relationship(back_populates="provider")
    territory_offices: List["TerritoryOffice"] = Relationship(back_populates="provider")
    agent_assignments: List["AgentProviderAssignment"] = Relationship(back_populates="provider")
    provider_wallets: List["ProviderWallet"] = Relationship(back_populates="provider")
    transactions: List["TransactionStream"] = Relationship(back_populates="provider")
    alerts: List["Alert"] = Relationship(back_populates="provider")
    tickets: List["Ticket"] = Relationship(back_populates="provider")


class Division(SQLModel, table=True):
    div_id: str = Field(primary_key=True)
    div_name: str

    risk_analysts: List["RiskAnalyst"] = Relationship(back_populates="division")
    territory_offices: List["TerritoryOffice"] = Relationship(back_populates="division")


class RiskAnalyst(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    provider_id: str = Field(foreign_key="provider.id")
    div_id: str = Field(foreign_key="division.div_id")
    area_name: str
    username: Optional[str] = Field(default=None, index=True)
    password: Optional[str] = Field(default=None)

    provider: Optional[Provider] = Relationship(back_populates="risk_analysts")
    division: Optional[Division] = Relationship(back_populates="risk_analysts")
    territory_offices: List["TerritoryOffice"] = Relationship(back_populates="risk_analyst")


class TerritoryOffice(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    provider_id: str = Field(foreign_key="provider.id")
    div_id: str = Field(foreign_key="division.div_id")
    area_name: str
    risk_analyst_id: str = Field(foreign_key="riskanalyst.id")
    username: Optional[str] = Field(default=None, index=True)
    password: Optional[str] = Field(default=None)

    provider: Optional[Provider] = Relationship(back_populates="territory_offices")
    division: Optional[Division] = Relationship(back_populates="territory_offices")
    risk_analyst: Optional[RiskAnalyst] = Relationship(back_populates="territory_offices")
    assignments: List["AgentProviderAssignment"] = Relationship(back_populates="territory_office")
    tickets: List["Ticket"] = Relationship(back_populates="assigned_officer")

class Agent(SQLModel, table=True): 
    agent_id: str = Field(primary_key=True)
    shop_name: str
    area: str
    district: str
    shared_physical_cash: Decimal
    status: AgentStatus
    username: Optional[str] = Field(default=None, index=True)
    password: Optional[str] = Field(default=None)

    provider_assignments: List["AgentProviderAssignment"] = Relationship(back_populates="agent")
    wallets: List["ProviderWallet"] = Relationship(back_populates="agent")
    transactions: List["TransactionStream"] = Relationship(back_populates="agent")
    alerts: List["Alert"] = Relationship(back_populates="agent")


class AgentProviderAssignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(foreign_key="agent.agent_id")
    provider_id: str = Field(foreign_key="provider.id")
    to_officer_id: str = Field(foreign_key="territoryoffice.id")

    agent: Optional[Agent] = Relationship(back_populates="provider_assignments")
    provider: Optional[Provider] = Relationship(back_populates="agent_assignments")
    territory_office: Optional[TerritoryOffice] = Relationship(back_populates="assignments")


class ProviderWallet(SQLModel, table=True):
    wallet_id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agent.agent_id")
    provider_id: str = Field(foreign_key="provider.id")
    e_money_balance: Decimal
    last_sync_time: datetime

    agent: Optional[Agent] = Relationship(back_populates="wallets")
    provider: Optional[Provider] = Relationship(back_populates="provider_wallets")


class TransactionStream(SQLModel, table=True):
    tx_id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agent.agent_id")
    provider_id: str = Field(foreign_key="provider.id")
    customer_id_hash: str
    tx_type: TransactionType
    tx_day: Optional[str] = None # Normal-Day, Eid-eve, Pay-Day
    amount: Decimal
    timestamp: datetime

    agent: Optional[Agent] = Relationship(back_populates="transactions")
    provider: Optional[Provider] = Relationship(back_populates="transactions")


class Alert(SQLModel, table=True):
    alert_id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agent.agent_id")
    provider_id: Optional[str] = Field(default=None, foreign_key="provider.id")
    alert_type: AlertType
    severity: SeverityLevel
    message_bn: str
    confidence_score: Decimal
    master_evidence_json: str
    created_at: datetime

    agent: Optional[Agent] = Relationship(back_populates="alerts")
    provider: Optional[Provider] = Relationship(back_populates="alerts")
    tickets: List["Ticket"] = Relationship(back_populates="alert")


class Ticket(SQLModel, table=True):
    ticket_id: str = Field(primary_key=True)
    alert_id: str = Field(foreign_key="alert.alert_id")
    provider_id: str = Field(foreign_key="provider.id")
    assigned_officer_id: str = Field(foreign_key="territoryoffice.id")
    current_owner_role: OwnerRole
    status: TicketStatus
    evidence_json: str
    created_at: datetime
    updated_at: datetime

    alert: Optional[Alert] = Relationship(back_populates="tickets")
    provider: Optional[Provider] = Relationship(back_populates="tickets")
    assigned_officer: Optional[TerritoryOffice] = Relationship(back_populates="tickets")
    audit_logs: List["AuditLog"] = Relationship(back_populates="ticket")


class AuditLog(SQLModel, table=True):
    log_id: str = Field(primary_key=True)
    ticket_id: str = Field(foreign_key="ticket.ticket_id")
    action_taken: AuditAction
    performed_by_role: str
    notes_text: str
    timestamp: datetime

    ticket: Optional[Ticket] = Relationship(back_populates="audit_logs")
