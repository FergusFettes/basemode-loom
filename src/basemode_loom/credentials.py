"""Provider API key storage for the HTTP API.

Keys live in basemode's own store (``~/.config/basemode/auth.json``, mode
0600) via :mod:`basemode.keys`, rather than in a second loom-specific store.
That means a key set through the API is the same key the ``basemode`` CLI and
the loom TUI already use, and generation picks it up with no extra wiring.

Nothing in this module returns a stored key. Reads are masked; the only raw
value that ever crosses the API boundary is the one the caller just sent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from basemode.keys import KEY_ALIASES, get_key, set_key

# Generous enough for any real provider key; small enough that a stray large
# body cannot be parked in the auth file.
MAX_KEY_BYTES = 8192

Source = str  # "stored" | "environment"


@dataclass(frozen=True)
class ProviderStatus:
    """What the API may say about a provider's key — never the key itself."""

    provider: str
    env_var: str
    configured: bool
    masked: str | None
    source: Source | None


def known_providers() -> tuple[str, ...]:
    """Providers the API accepts keys for, from basemode's own alias table.

    Restricting writes to this set keeps callers from naming an arbitrary
    environment variable, and gives the frontend a definitive list to render.
    A provider litellm supports but basemode has no alias for cannot be set
    here until ``KEY_ALIASES`` grows an entry for it.
    """
    return tuple(sorted(KEY_ALIASES))


def is_known_provider(provider: str) -> bool:
    return provider.strip().lower() in KEY_ALIASES


def env_var_for(provider: str) -> str:
    return KEY_ALIASES[provider.strip().lower()]


def mask(value: str) -> str:
    """Render a key for display without revealing enough to reconstruct it."""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def provider_status(provider: str) -> ProviderStatus:
    """Report how (if at all) a provider's key is configured.

    A key already exported in the environment is reported as configured too,
    so the UI can explain why generation works without anything stored.
    """
    name = provider.strip().lower()
    env_var = env_var_for(name)

    stored = get_key(name)
    if stored:
        return ProviderStatus(name, env_var, True, mask(stored), "stored")

    from_environment = os.environ.get(env_var)
    if from_environment:
        return ProviderStatus(
            name, env_var, True, mask(from_environment), "environment"
        )

    return ProviderStatus(name, env_var, False, None, None)


def list_provider_status() -> list[ProviderStatus]:
    return [provider_status(provider) for provider in known_providers()]


def store_provider_key(provider: str, value: str) -> ProviderStatus:
    """Persist a provider key and make it usable by the running process.

    ``basemode.continue_`` calls ``load_into_environ()`` before each
    generation, so writing the file alone is enough for a *first* key. It is
    not enough for a *replacement*: that helper deliberately skips any
    variable already set, so an earlier key — exported by a previous
    generation, or by the user's shell — would keep shadowing the new one
    for the life of the process. Setting the variable here is what makes an
    update take effect, and makes the result immediate either way rather
    than dependent on when generation next runs.
    """
    name = provider.strip().lower()
    set_key(name, value)
    os.environ[env_var_for(name)] = value
    return provider_status(name)
