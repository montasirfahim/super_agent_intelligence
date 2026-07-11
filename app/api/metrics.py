from fastapi import APIRouter

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics():
    return {
        "liquidity": {"available_cash": 1500000.0, "velocity": 0.18},
        "shared_cash_state": {"status": "stable", "buffer": 120000.0},
    }
