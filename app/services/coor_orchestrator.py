from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OrchestrationState:
    status: str = "idle"
    notes: list[str] | None = None


class CoordinationOrchestrator:
    def __init__(self) -> None:
        self.state = OrchestrationState()

    def advance(self, payload: dict[str, Any]) -> OrchestrationState:
        self.state.status = "processing"
        self.state.notes = [payload.get("note", "No note provided")]
        return self.state
