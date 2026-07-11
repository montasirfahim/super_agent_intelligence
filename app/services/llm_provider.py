from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.core.config import settings


class StructuredLLMProvider:
    def __init__(self, provider: str = "openai") -> None:
        self.provider = provider
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def invoke(self, prompt: str) -> dict[str, Any]:
        if not self.client:
            return {"summary": "OpenAI client not configured", "provider": self.provider}

        response = self.client.responses.create(
            model=settings.model_name,
            input=prompt,
        )
        return {"summary": response.output_text, "provider": self.provider}
