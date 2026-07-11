from fastapi import HTTPException


class ServiceError(Exception):
    """Base app-specific error."""


class GuardrailViolationError(ServiceError):
    """Raised when a request violates responsible-AI guardrails."""


class LatencyFallbackError(ServiceError):
    """Raised when a service cannot produce fresh telemetry on time."""


def map_error(error: Exception) -> tuple[int, dict]:
    if isinstance(error, GuardrailViolationError):
        return 403, {"error": "guardrail_violation", "detail": str(error)}
    if isinstance(error, LatencyFallbackError):
        return 504, {"error": "latency_fallback", "detail": str(error)}
    if isinstance(error, HTTPException):
        return error.status_code, {"error": "http_error", "detail": error.detail}
    return 500, {"error": "internal_server_error", "detail": str(error)}
