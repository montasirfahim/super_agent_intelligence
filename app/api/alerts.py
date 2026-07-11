from fastapi import APIRouter, HTTPException

from app.schemas.request import StateChangeRequest
from app.schemas.response import ExplainableAnomaly

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[ExplainableAnomaly])
def list_alerts():
    return [
        ExplainableAnomaly(
            alert_id="alert-001",
            title="Liquidity runoff detected",
            severity="high",
            explanation="Outbound transfers exceed available cash buffers.",
            recommended_action="Freeze discretionary disbursements and validate settlement queues.",
        )
    ]


@router.post("", response_model=ExplainableAnomaly)
def create_alert(payload: StateChangeRequest):
    if payload.alert_status == "blocked":
        raise HTTPException(status_code=409, detail="alert already blocked")

    return ExplainableAnomaly(
        alert_id="alert-002",
        title="Workflow state updated",
        severity=payload.severity,
        explanation="The coordinated response pathway was advanced by the orchestrator.",
        recommended_action="Escalate to the appropriate control officer.",
    )
