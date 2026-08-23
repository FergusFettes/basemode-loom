"""Shared model-plan representation, validation, and persistence helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MIN_MAX_TOKENS = 1
MAX_MAX_TOKENS = 8000
MAX_BRANCHES_PER_MODEL = 64


@dataclass(frozen=True)
class ModelPlanEntry:
    model: str
    n_branches: int
    max_tokens: int
    temperature: float
    enabled: bool = True
    pinned_settings: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "n_branches": self.n_branches,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "enabled": self.enabled,
            "pinned_settings": self.pinned_settings,
        }


def normalize_model_plan(raw_plan: Any) -> list[dict[str, Any]]:
    """Return valid persisted entries, dropping malformed legacy entries."""
    if not isinstance(raw_plan, list):
        return []
    plan: list[dict[str, Any]] = []
    for entry in raw_plan:
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model", "")).strip()
        if not model:
            continue
        try:
            n_branches = max(1, int(entry.get("n_branches", 1)))
            max_tokens = max(
                MIN_MAX_TOKENS,
                min(int(entry.get("max_tokens", 200)), MAX_MAX_TOKENS),
            )
            temperature = float(entry.get("temperature", 0.9))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(temperature):
            continue
        plan.append(
            ModelPlanEntry(
                model=model,
                n_branches=n_branches,
                max_tokens=max_tokens,
                temperature=temperature,
                enabled=bool(entry.get("enabled", True)),
                pinned_settings=bool(entry.get("pinned_settings", False)),
            ).as_dict()
        )
    return plan


def parse_model_plan(raw_plan: Any) -> list[ModelPlanEntry]:
    """Parse persisted plan data into the runtime representation."""
    return [ModelPlanEntry(**entry) for entry in normalize_model_plan(raw_plan)]


def validate_model_plan(
    raw_plan: Any,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate an untrusted API model plan without silently coercing it."""
    if not isinstance(raw_plan, list) or not raw_plan:
        return None, "must be a non-empty list"
    parsed: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_plan):
        field = f"model_plan[{idx}]"
        if not isinstance(entry, dict):
            return None, f"{field} must be an object"
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip():
            return None, f"{field}.model must be a non-empty string"
        n_branches = entry.get("n_branches", 1)
        if not _is_int(n_branches) or not 1 <= n_branches <= MAX_BRANCHES_PER_MODEL:
            return (
                None,
                f"{field}.n_branches must be an integer between 1 and {MAX_BRANCHES_PER_MODEL}",
            )
        max_tokens = entry.get("max_tokens", 200)
        if (
            not _is_int(max_tokens)
            or not MIN_MAX_TOKENS <= max_tokens <= MAX_MAX_TOKENS
        ):
            return (
                None,
                f"{field}.max_tokens must be an integer between {MIN_MAX_TOKENS} and {MAX_MAX_TOKENS}",
            )
        temperature = entry.get("temperature", 0.9)
        if (
            not _is_number(temperature)
            or not math.isfinite(float(temperature))
            or not 0.0 <= float(temperature) <= 2.0
        ):
            return None, f"{field}.temperature must be a number between 0 and 2"
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            return None, f"{field}.enabled must be a boolean"
        pinned_settings = entry.get("pinned_settings", False)
        if not isinstance(pinned_settings, bool):
            return None, f"{field}.pinned_settings must be a boolean"
        parsed.append(
            ModelPlanEntry(
                model=model.strip(),
                n_branches=n_branches,
                max_tokens=max_tokens,
                temperature=float(temperature),
                enabled=enabled,
                pinned_settings=pinned_settings,
            ).as_dict()
        )
    return parsed, None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
