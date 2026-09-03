"""Basemode call-observation provenance for Loom."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from basemode import ObservationContext


def _loom_version() -> str:
    try:
        return version("basemode-loom")
    except PackageNotFoundError:  # pragma: no cover - source tree without install
        return "unknown"


def loom_observation() -> ObservationContext:
    """Return the allow-listed provenance attached to every Loom call."""
    return ObservationContext(
        source="loom",
        source_version=_loom_version(),
        contribution_eligible=False,
    )
