from pydantic import BaseModel, Field

from app.schemas.enums import AlertStatus, Severity


class StateChangeRequest(BaseModel):
    alert_status: AlertStatus | str = Field(default=AlertStatus.OPEN)
    severity: Severity | str = Field(default=Severity.MEDIUM)
    note: str | None = Field(default=None)


class SimulationTrigger(BaseModel):
    scenario_id: str = Field(default="scenario-001")
    risk_score: float = Field(default=0.7)
    projected_runway_hours: int = Field(default=12)
    trigger_reason: str = Field(default="scheduled")
