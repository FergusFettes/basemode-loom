"""Model-ID resolution helpers.

Supports explicit OpenRouter forcing for newly-released models that may not
yet exist in LiteLLM/basemode model maps.
"""

from __future__ import annotations

from basemode.detect import normalize_model


def resolve_model_id(model: str) -> str:
    """Resolve a model id, with explicit OpenRouter force prefixes.

    Supported force syntaxes:
    - ``openrouter:vendor/model``
    - ``or:vendor/model``
    """
    raw = model.strip()
    if raw.startswith("openrouter:"):
        suffix = raw.split(":", 1)[1].lstrip("/")
        return f"openrouter/{suffix}"
    if raw.startswith("or:"):
        suffix = raw.split(":", 1)[1].lstrip("/")
        return f"openrouter/{suffix}"
    return normalize_model(raw)
