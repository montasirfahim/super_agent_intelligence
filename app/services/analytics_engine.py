from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LiquiditySignal:
    available_cash: float
    velocity: float
    runway_hours: int


class TimeToDepletionAnalytics:
    def __init__(self, threshold: float = 0.2):
        self.threshold = threshold

    def compute_signal(self, available_cash: float, velocity: float) -> LiquiditySignal:
        runway_hours = max(1, int(available_cash / max(velocity, 1e-6)))
        return LiquiditySignal(
            available_cash=available_cash,
            velocity=velocity,
            runway_hours=runway_hours,
        )

    def is_runaway(self, signal: LiquiditySignal) -> bool:
        return signal.velocity >= self.threshold
