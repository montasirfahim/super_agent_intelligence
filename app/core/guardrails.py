import re
from app.schemas.enums import Severity

class SafetyGuardrailEngine:
    def __init__(self):
        self.prohibited_terms = [
            re.compile(r'\bfraud\b', re.IGNORECASE),
            re.compile(r'\btheft\b', re.IGNORECASE),
            re.compile(r'\bcriminal\b', re.IGNORECASE),
            re.compile(r'\bscam\b', re.IGNORECASE),
            re.compile(r'\bthief\b', re.IGNORECASE)
        ]

    def enforce_responsible_language(self, text: str) -> str:
        sanitized = text
        for pattern in self.prohibited_terms:
            sanitized = pattern.sub("unusual operational pattern", sanitized)
        return sanitized

    def calculate_adjusted_severity(self, base_severity: Severity, data_confidence: float) -> Severity:
        if data_confidence < 0.6:
            return Severity.LOW
        return base_severity