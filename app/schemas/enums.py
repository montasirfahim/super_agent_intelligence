from enum import Enum


class LLMProvider(str, Enum):
    OPENAI = "openai"
    GROQ = "groq"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    BLOCKED = "BLOCKED"
    UNASSIGNED = "UNASSIGNED"
    RESOLVED = "RESOLVED"


class MFSProvider(str, Enum):
    BKASH = "bKash"
    NAGAD = "Nagad"
    ROCKET = "Rocket"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"