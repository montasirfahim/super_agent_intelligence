from app.core.errors import GuardrailViolationError


class GuardrailPolicy:
    prohibited_terms = {
        "fraud",
        "scam",
        "money laundering",
        "illicit transfers",
    }

    @classmethod
    def enforce(cls, text: str) -> None:
        lowered = text.lower()
        for term in cls.prohibited_terms:
            if term in lowered:
                raise GuardrailViolationError(
                    f"Request content references prohibited terminology: {term}"
                )
