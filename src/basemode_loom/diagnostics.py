from __future__ import annotations

import uuid
from dataclasses import dataclass

from basemode.exceptions import EmptyCompletionError
from basemode.failure_taxonomy import classify_error, error_details


@dataclass(frozen=True)
class ProviderDiagnostic:
    incident_id: str
    category: str
    status: int | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    error_param: str | None = None


def provider_diagnostic(error: BaseException) -> ProviderDiagnostic:
    """Classify a provider failure, and give it an ID a user can quote.

    Categories come from Basemode's public failure taxonomy, so user-facing
    diagnostics use the same labels as its authoritative call ledger.
    """
    if isinstance(error, EmptyCompletionError):
        return ProviderDiagnostic(
            uuid.uuid4().hex, "empty_response", None, error.finish_reason
        )
    category, status = classify_error(error)
    error_code, error_param = error_details(error)
    return ProviderDiagnostic(
        uuid.uuid4().hex,
        category,
        status,
        error_code=error_code,
        error_param=error_param,
    )


def empty_response_diagnostic() -> ProviderDiagnostic:
    """A provider stream that ended cleanly but produced no usable text.

    Distinct from `provider_diagnostic`, which classifies a raised exception:
    this covers a branch the provider reports as successful yet returns
    nothing (or only content that normalizes away to nothing), which would
    otherwise persist a silent empty node.
    """
    return ProviderDiagnostic(uuid.uuid4().hex, "empty_response", None)
