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
import json
import os
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
    toggle_chat_headers: str = "N"
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
    model: str = "zai/glm-5.2"
    max_tokens: int = 20
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


@dataclass
class ServerConfig:
    production: bool = False
    allowed_origins: list[str] = field(default_factory=list)
    max_message_bytes: int = 4 * 1024 * 1024
    max_field_bytes: int = 1024 * 1024
    max_context_tokens: int = 128_000
    concurrent_generation_jobs: int = 32
    # Per connection, not server-wide: how many generations one client may
    # have running at once (in different places in the tree, typically).
    # `concurrent_generation_jobs` still caps the whole server.
    concurrent_generations_per_session: int = 10
    max_branches_per_job: int = 64
    generation_timeout_seconds: float = 60.0
    max_output_tokens: int = 8000
    enable_docs: bool | None = None
    # Storing a provider key writes it to the machine-wide basemode auth file,
    # which every caller of this server then generates with. That is what a
    # local single-user tool wants, and wrong for a shared deployment, so
    # writes default off whenever production is on. Set explicitly to override
    # in either direction.
    allow_credential_writes: bool | None = None
    # Model ratings land in the same machine-wide basemode config file. They
    # are preferences rather than secrets, but they are still the operator's
    # preferences, so writes follow the same production default.
    allow_rating_writes: bool | None = None

    def credential_writes_enabled(self) -> bool:
        if self.allow_credential_writes is not None:
            return self.allow_credential_writes
        return not self.production

    def rating_writes_enabled(self) -> bool:
        if self.allow_rating_writes is not None:
            return self.allow_rating_writes
        return not self.production


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    keys: KeyMap = field(default_factory=KeyMap)
    defaults: GenerationDefaults = field(default_factory=GenerationDefaults)
    models: dict[str, ModelConfig] = field(default_factory=dict)
    server: ServerConfig = field(default_factory=ServerConfig)

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
    """Load config from files and BASEMODE_LOOM_* environment overrides."""
    data: dict = {}
    for path in (user_config_path(), project_config_path()):
        if path.exists():
            with open(path, "rb") as f:
                data = _deep_merge(data, tomllib.load(f))
    return _parse_config(_deep_merge(data, _server_environment_config()))


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
        server=_parse_dataclass(ServerConfig, data.get("server", {})),
    )


def _parse_dataclass(cls, data: dict):
    """Populate a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def _server_environment_config() -> dict:
    prefix = "BASEMODE_LOOM_"
    parsers = {
        "PRODUCTION": ("production", _parse_bool),
        "ALLOWED_ORIGINS": ("allowed_origins", _parse_origins),
        "MAX_MESSAGE_BYTES": ("max_message_bytes", int),
        "MAX_FIELD_BYTES": ("max_field_bytes", int),
        "MAX_CONTEXT_TOKENS": ("max_context_tokens", int),
        "CONCURRENT_GENERATION_JOBS": ("concurrent_generation_jobs", int),
        "CONCURRENT_GENERATIONS_PER_SESSION": (
            "concurrent_generations_per_session",
            int,
        ),
        "MAX_BRANCHES_PER_JOB": ("max_branches_per_job", int),
        "GENERATION_TIMEOUT_SECONDS": ("generation_timeout_seconds", float),
        "MAX_OUTPUT_TOKENS": ("max_output_tokens", int),
        "ENABLE_DOCS": ("enable_docs", _parse_bool),
        "ALLOW_CREDENTIAL_WRITES": ("allow_credential_writes", _parse_bool),
        "ALLOW_RATING_WRITES": ("allow_rating_writes", _parse_bool),
    }
    server: dict = {}
    for suffix, (key, parser) in parsers.items():
        raw = os.environ.get(prefix + suffix)
        if raw is not None:
            server[key] = parser(raw)
    return {"server": server} if server else {}


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {raw!r}")


def _parse_origins(raw: str) -> list[str]:
    value = raw.strip()
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
            raise ValueError("BASEMODE_LOOM_ALLOWED_ORIGINS must be a string list")
        return parsed
    return [item.strip() for item in value.split(",") if item.strip()]
