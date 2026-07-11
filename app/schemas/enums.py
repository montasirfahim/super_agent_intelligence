from enum import Enum


class Provider(str, Enum):
    OPENAI = "openai"
    GROQ = "groq"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    BLOCKED = "blocked"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
