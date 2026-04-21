import pytest

from basemode_loom import model_resolver


def test_resolve_model_id_supports_openrouter_force_prefix() -> None:
    assert (
        model_resolver.resolve_model_id("openrouter:moonshotai/kimi-k2.6")
        == "openrouter/moonshotai/kimi-k2.6"
    )


def test_resolve_model_id_supports_short_or_force_prefix() -> None:
    assert (
        model_resolver.resolve_model_id("or:moonshotai/kimi-k2.6")
        == "openrouter/moonshotai/kimi-k2.6"
    )


def test_resolve_model_id_uses_normalize_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "basemode_loom.model_resolver.normalize_model",
        lambda model: f"normalized::{model}",
    )
    assert model_resolver.resolve_model_id("gpt-4o-mini") == "normalized::gpt-4o-mini"


def test_resolve_model_id_raises_when_unforced_unknown(monkeypatch) -> None:
    def _raise(_: str) -> str:
        raise ValueError("unknown model")

    monkeypatch.setattr("basemode_loom.model_resolver.normalize_model", _raise)
    with pytest.raises(ValueError, match="unknown model"):
        model_resolver.resolve_model_id("moonshotai/kimi-k2.6")
