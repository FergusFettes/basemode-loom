from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDiagnostic:
    incident_id: str
    category: str
    status: int | None = None


def provider_diagnostic(error: BaseException) -> ProviderDiagnostic:
    status = _status_code(error)
    name = type(error).__name__.lower()
    if status in {401, 403} or "auth" in name or "permission" in name:
        category = "authentication"
    elif status == 429 or "ratelimit" in name or "rate_limit" in name:
        category = "rate_limit"
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
        category = "timeout"
    elif status is not None and status >= 500:
        category = "provider_unavailable"
    elif status in {400, 404, 409, 422}:
        category = "invalid_request"
    elif "connection" in name or "network" in name:
        category = "network"
    else:
        category = "provider_error"
    return ProviderDiagnostic(uuid.uuid4().hex, category, status)


def _status_code(error: BaseException) -> int | None:
    candidates = (getattr(error, "status_code", None), getattr(error, "status", None))
    response = getattr(error, "response", None)
    if response is not None:
        candidates += (getattr(response, "status_code", None),)
    for value in candidates:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 100 <= value <= 599
        ):
            return value
    return None
