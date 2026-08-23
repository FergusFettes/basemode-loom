from __future__ import annotations

from basemode_loom.model_plan import normalize_model_plan, validate_model_plan


def test_normalize_model_plan_drops_malformed_persisted_entries() -> None:
    assert normalize_model_plan(
        [
            {"model": "valid", "temperature": 0.4},
            {"model": "bad-number", "max_tokens": "nope"},
            {"model": "bad-temperature", "temperature": float("nan")},
        ]
    ) == [
        {
            "model": "valid",
            "n_branches": 1,
            "max_tokens": 200,
            "temperature": 0.4,
            "enabled": True,
            "pinned_settings": False,
        }
    ]


def test_validate_model_plan_rejects_non_finite_temperature() -> None:
    plan, error = validate_model_plan([{"model": "model", "temperature": float("inf")}])

    assert plan is None
    assert error == "model_plan[0].temperature must be a number between 0 and 2"


def test_model_plan_preserves_per_model_pin_setting() -> None:
    plan, error = validate_model_plan([{"model": "model", "pinned_settings": False}])

    assert error is None
    assert plan is not None
    assert plan[0]["pinned_settings"] is False
