"""User configuration: keybindings, generation defaults, per-model overrides.

Config is loaded from (in order, later overrides earlier):
  ~/.config/basemode-loom/config.toml   — user-level
  ./.basemode-loom.toml                  — project-level

Example config.toml:

    [keys]
    generate = "space"
    quick_generate = "shift+space"
    numeric_branch_shortcuts = true

    [defaults]
    model = "gpt-4o-mini"
    max_tokens = 200
    temperature = 0.9
    n_branches = 1
    model_overrides = true

    [model."gpt-4o"]
    n_branches = 3

    [model."claude-opus-4-7"]
    n_branches = 2
    temperature = 0.8
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# KeyMap
# ---------------------------------------------------------------------------


@dataclass
class KeyMap:
    # Tree navigation
    nav_parent: str = "h"
    nav_child: str = "l"
    nav_next: str = "j"
    nav_prev: str = "k"
    # Word cursor (move within current text to pick a truncation point)
    word_prev: str = "H"
    word_next: str = "L"
    # Generation / editing
    generate: str = "space"
    quick_generate: str = "shift+space"
    numeric_branch_shortcuts: bool = True
    edit: str = "e"
    edit_full: str = "E"
    edit_context: str = "c"
    # Model / params
    pick_model: str = "m"
    tokens_up: str = "w"
    tokens_down: str = "s"
    set_tokens: str = "t"
    branches_up: str = "d"
    branches_down: str = "a"
    # View
    toggle_tree_view: str = "v"
    toggle_model_names: str = "n"
    toggle_hoist: str = "Z"
    # Bookmarks
    toggle_bookmark: str = "b"
    next_bookmark: str = "B"
    # App
    open_picker: str = "tab"
    open_stats: str = "?"
    open_config_review: str = "C"
    open_prompt: str = "p"
    quit: str = "q"
    cancel_or_quit: str = "escape"


# ---------------------------------------------------------------------------
# GenerationDefaults
# ---------------------------------------------------------------------------


@dataclass
class GenerationDefaults:
    model: str = "gpt-4o-mini"
    max_tokens: int = 200
    temperature: float = 0.9
    n_branches: int = 1
    model_overrides: bool = True


# ---------------------------------------------------------------------------
# ModelConfig  (per-model partial overrides)
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    n_branches: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    keys: KeyMap = field(default_factory=KeyMap)
    defaults: GenerationDefaults = field(default_factory=GenerationDefaults)
    models: dict[str, ModelConfig] = field(default_factory=dict)

    def effective_defaults(self, model_id: str) -> GenerationDefaults:
        """Return generation defaults for a specific model.

        When model_overrides is enabled, per-model config is merged on top of
        the global defaults. Falls back to short model name (after last '/').
        """
        if not self.defaults.model_overrides:
            return self.defaults
        mc = self.models.get(model_id) or self.models.get(model_id.split("/")[-1])
        if mc is None:
            return self.defaults
        return GenerationDefaults(
            model=self.defaults.model,
            max_tokens=mc.max_tokens
            if mc.max_tokens is not None
            else self.defaults.max_tokens,
            temperature=mc.temperature
            if mc.temperature is not None
            else self.defaults.temperature,
            n_branches=mc.n_branches
            if mc.n_branches is not None
            else self.defaults.n_branches,
            model_overrides=self.defaults.model_overrides,
        )


DEFAULT_CONFIG = Config()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def user_config_path() -> Path:
    return Path.home() / ".config" / "basemode-loom" / "config.toml"


def project_config_path() -> Path:
    return Path(".basemode-loom.toml")


def load_config() -> Config:
    """Load config from user and project files, project overriding user."""
    data: dict = {}
    for path in (user_config_path(), project_config_path()):
        if path.exists():
            with open(path, "rb") as f:
                data = _deep_merge(data, tomllib.load(f))
    return _parse_config(data)


# ---------------------------------------------------------------------------
# Serialization (for the API endpoint)
# ---------------------------------------------------------------------------


def config_to_dict(config: Config) -> dict:
    """Serialize Config to a JSON-friendly dict."""
    return {
        "keys": dataclasses.asdict(config.keys),
        "defaults": dataclasses.asdict(config.defaults),
        "models": {
            name: {k: v for k, v in dataclasses.asdict(mc).items() if v is not None}
            for name, mc in config.models.items()
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _parse_config(data: dict) -> Config:
    return Config(
        keys=_parse_dataclass(KeyMap, data.get("keys", {})),
        defaults=_parse_dataclass(GenerationDefaults, data.get("defaults", {})),
        models={
            name: _parse_dataclass(ModelConfig, model_data)
            for name, model_data in data.get("model", {}).items()
        },
    )


def _parse_dataclass(cls, data: dict):
    """Populate a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})
