from pydantic import BaseModel, Field


class LiquidityRunawayCard(BaseModel):
    scenario_id: str
    risk_score: float
    projected_runway_hours: int
    trigger_reason: str
    recommended_controls: list[str] = Field(default_factory=list)


class ExplainableAnomaly(BaseModel):
    alert_id: str
    title: str
    severity: str
    explanation: str
    recommended_action: str
