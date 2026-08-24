"""Publish privacy-preserving Loom corpus statistics to basemode."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .store import GenerationStore


@dataclass(frozen=True)
class CorpusObservation:
    """An aggregate with no tree, node, prompt, or generated text identifiers."""

    model: str
    prompt_method: str | None
    depth_bucket: str
    issue_kind: str
    generated_count: int
    successful_count: int
    flagged_count: int
    corrected_count: int
    open_issue_count: int
    timed_count: int
    timing_summary: dict[str, float | int | None]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        timed_count = payload.pop("timed_count")
        payload["timing_summary"]["timed_count"] = timed_count
        return payload


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def default_source_instance(path: Path) -> str:
    """Return a stable, non-reversible identifier for one local corpus."""
    identity = f"{platform.node()}\0{path.expanduser().resolve()}".encode()
    return "loom-" + hashlib.sha256(identity).hexdigest()[:16]


def collect_corpus_observations(
    store: GenerationStore,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[CorpusObservation]:
    """Aggregate generated nodes without returning private corpus material.

    A boundary edit is a corrected issue. An explicit flag with no edit is an
    open issue. ``flagged_count`` is their union, so corrected and unresolved
    problems remain independently queryable without double counting a node.
    """
    rows = store.corpus_evidence_rows(window_start=window_start, window_end=window_end)
    return [CorpusObservation(**row) for row in rows]


def publish_corpus_statistics(
    store: GenerationStore,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    source_instance: str | None = None,
) -> int:
    """Publish aggregates through basemode's evidence API.

    A clear error on older basemode releases is preferable to silently writing
    a second evidence format. Collecting and JSON preview remain available.
    """
    try:
        from basemode.evidence import publish_corpus_observations
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "this command needs a basemode release with the model-evidence API"
        ) from exc

    observations = collect_corpus_observations(
        store, window_start=window_start, window_end=window_end
    )
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    result = publish_corpus_observations(
        [item.to_dict() for item in observations],
        source_instance=source_instance or default_source_instance(store.db_path),
        window_start=window_start,
        window_end=window_end or now,
        loom_version=_package_version("basemode-loom"),
        basemode_version=_package_version("basemode"),
    )
    return int(result)


def observations_json(observations: list[CorpusObservation]) -> str:
    """Serialize an audit preview deterministically."""
    return json.dumps(
        [item.to_dict() for item in observations], indent=2, sort_keys=True
    )
