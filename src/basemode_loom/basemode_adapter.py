"""Temporary compatibility boundary for basemode call observations."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from basemode.continue_ import branch_text as _branch_text
from basemode.continue_ import continue_text as _continue_text

try:
    from basemode import ObservationContext as _ObservationContext
except ImportError:
    _ObservationContext = None


@dataclass(frozen=True)
class _LegacyObservationContext:
    source: str
    source_version: str
    contribution_eligible: bool = False


def _loom_version() -> str:
    try:
        return version("basemode-loom")
    except PackageNotFoundError:  # pragma: no cover - source tree without install
        return "0.7.13"


def loom_observation() -> Any:
    """Return the allow-listed provenance attached to every Loom call."""
    context_type = _ObservationContext or _LegacyObservationContext
    return context_type(
        source="loom",
        source_version=_loom_version(),
        contribution_eligible=False,
    )


async def continue_text(*args: Any, observation: Any, **kwargs: Any) -> AsyncGenerator:
    """Call basemode with provenance, omitting it only on the legacy API."""
    if _ObservationContext is not None:
        kwargs["observation"] = observation
    async for token in _continue_text(*args, **kwargs):
        yield token


async def branch_text(*args: Any, observation: Any, **kwargs: Any) -> AsyncGenerator:
    """Call basemode's branch API with the same provenance contract."""
    if _ObservationContext is not None:
        kwargs["observation"] = observation
    async for item in _branch_text(*args, **kwargs):
        yield item
