from __future__ import annotations

import uuid
from dataclasses import dataclass

from basemode.exceptions import EmptyCompletionError
from basemode.health import classify_error


@dataclass(frozen=True)
class ProviderDiagnostic:
    incident_id: str
    category: str
    status: int | None = None
    finish_reason: str | None = None


def provider_diagnostic(error: BaseException) -> ProviderDiagnostic:
    """Classify a provider failure, and give it an ID a user can quote.

    Categories come from `basemode.health.classify_error`, so the label shown
    in the UI is the same label recorded against the model's health.
    """
    if isinstance(error, EmptyCompletionError):
        return ProviderDiagnostic(
            uuid.uuid4().hex, "empty_response", None, error.finish_reason
        )
    category, status = classify_error(error)
    return ProviderDiagnostic(uuid.uuid4().hex, category, status)


def empty_response_diagnostic() -> ProviderDiagnostic:
    """A provider stream that ended cleanly but produced no usable text.

    Distinct from `provider_diagnostic`, which classifies a raised exception:
    this covers a branch the provider reports as successful yet returns
    nothing (or only content that normalizes away to nothing), which would
    otherwise persist a silent empty node.
    """
    return ProviderDiagnostic(uuid.uuid4().hex, "empty_response", None)
