from collections import deque
from typing import Any


class LatencyFallbackLayer:
    """Provide resilient fallback data when fresh telemetry is unavailable."""

    def __init__(self, window_size: int = 5):
        self._history: deque[dict[str, Any]] = deque(maxlen=window_size)

    def observe(self, payload: dict[str, Any]) -> None:
        self._history.append(payload)

    def latest(self) -> dict[str, Any] | None:
        if not self._history:
            return None
        return self._history[-1]
