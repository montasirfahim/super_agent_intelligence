from fastapi import APIRouter

from app.schemas.request import SimulationTrigger
from app.schemas.response import LiquidityRunawayCard

router = APIRouter(prefix="/simulate", tags=["simulation"])


@router.post("", response_model=LiquidityRunawayCard)
def simulate(payload: SimulationTrigger):
    return LiquidityRunawayCard(
        scenario_id=payload.scenario_id,
        risk_score=payload.risk_score,
        projected_runway_hours=payload.projected_runway_hours,
        trigger_reason=payload.trigger_reason,
        recommended_controls=["reconcile transfers", "increase contingency buffer"],
    )
