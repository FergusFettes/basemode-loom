"""Provider key storage over the API.

The load-bearing property under test is that a stored key never comes back
out: the only read surface is masked, and the raw value must not appear in
any response body, anywhere.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from basemode.keys import KEY_ALIASES
from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config, ServerConfig, load_config
from basemode_loom.store import GenerationStore

SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
ALLOWED = "https://grove.example.com"


@pytest.fixture(autouse=True)
def isolated_key_store(tmp_path, monkeypatch):
    """Point basemode's key file at tmp_path and isolate provider env vars.

    ``basemode.keys`` resolves its paths at import time, so patching HOME is
    not enough — the module globals have to be replaced directly.
    """
    auth_file = tmp_path / "auth.json"
    monkeypatch.setattr("basemode.keys._CONFIG_DIR", tmp_path)
    monkeypatch.setattr("basemode.keys._AUTH_FILE", auth_file)
    # setenv (rather than delenv) always records a restore entry, so the
    # direct os.environ write in store_provider_key gets cleaned up too.
    for env_var in KEY_ALIASES.values():
        monkeypatch.setenv(env_var, "")
    return auth_file


def _client(tmp_path, config: Config | None = None) -> TestClient:
    store = GenerationStore(tmp_path / "credentials.sqlite")
    return TestClient(create_app(store, config or Config()))


def _production_config(**overrides) -> Config:
    values = {"production": True, "allowed_origins": [ALLOWED]}
    values.update(overrides)
    return Config(server=ServerConfig(**values))


def test_lists_every_known_provider_as_unconfigured_initially(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/keys")

    assert response.status_code == 200
    body = response.json()
    assert body["writable"] is True
    assert {p["provider"] for p in body["providers"]} == set(KEY_ALIASES)
    assert all(p["configured"] is False for p in body["providers"])
    assert all(p["masked"] is None for p in body["providers"])
    assert all(p["source"] is None for p in body["providers"])


def test_storing_a_key_returns_only_a_masked_status(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/keys/openai", json={"value": SECRET})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "provider": "openai",
        "env_var": "OPENAI_API_KEY",
        "configured": True,
        "masked": "sk-p...6789",
        "source": "stored",
    }
    assert SECRET not in response.text


def test_stored_key_is_never_returned_by_any_read_surface(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.put("/api/keys/openai", json={"value": SECRET})

        listing = client.get("/api/keys")
        config = client.get("/api/config")

    assert SECRET not in listing.text
    assert SECRET not in config.text
    stored = next(p for p in listing.json()["providers"] if p["provider"] == "openai")
    assert stored["configured"] is True
    assert stored["masked"] == "sk-p...6789"
    assert stored["source"] == "stored"


def test_key_is_persisted_to_the_basemode_auth_file_with_owner_only_permissions(
    tmp_path, isolated_key_store
) -> None:
    with _client(tmp_path) as client:
        client.put("/api/keys/anthropic", json={"value": SECRET})

    saved = json.loads(isolated_key_store.read_text())
    assert saved["keys"]["anthropic"] == SECRET
    assert stat.S_IMODE(isolated_key_store.stat().st_mode) == 0o600


def test_stored_key_takes_effect_immediately_without_a_restart(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.put("/api/keys/openai", json={"value": SECRET})

    # basemode reloads the key file before each generation, so this matters
    # most for replacements: load_into_environ() skips variables that are
    # already set, so without this export a stale key would keep shadowing
    # the new one. See test_updating_a_key_replaces_the_previous_value.
    assert os.environ["OPENAI_API_KEY"] == SECRET


def test_updating_a_key_replaces_the_previous_value(
    tmp_path, isolated_key_store
) -> None:
    replacement = "sk-proj-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz9999"
    with _client(tmp_path) as client:
        client.put("/api/keys/openai", json={"value": SECRET})
        response = client.put("/api/keys/openai", json={"value": replacement})

    assert response.json()["masked"] == "sk-p...9999"
    assert json.loads(isolated_key_store.read_text())["keys"]["openai"] == replacement
    assert os.environ["OPENAI_API_KEY"] == replacement


def test_key_supplied_only_through_the_environment_is_reported_as_such(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    with _client(tmp_path) as client:
        response = client.get("/api/keys")

    groq = next(p for p in response.json()["providers"] if p["provider"] == "groq")
    assert groq["configured"] is True
    assert groq["source"] == "environment"
    assert SECRET not in response.text


def test_surrounding_whitespace_is_stripped_before_storing(
    tmp_path, isolated_key_store
) -> None:
    with _client(tmp_path) as client:
        client.put("/api/keys/openai", json={"value": f"  {SECRET}\n"})

    assert json.loads(isolated_key_store.read_text())["keys"]["openai"] == SECRET


def test_short_key_is_fully_elided_rather_than_partly_echoed(tmp_path) -> None:
    short = "sk-12345"
    with _client(tmp_path) as client:
        response = client.put("/api/keys/openai", json={"value": short})

    assert response.json()["masked"] == "***"
    assert short not in response.text


def test_unknown_provider_is_rejected(tmp_path, isolated_key_store) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/keys/not-a-provider", json={"value": SECRET})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"
    assert not isolated_key_store.exists()


def test_blank_key_is_rejected(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/keys/openai", json={"value": "   "})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "empty_key"


def test_oversized_key_is_rejected(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/keys/openai", json={"value": "x" * 9000})

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "key_too_large"


def test_production_refuses_key_writes_by_default(tmp_path) -> None:
    with _client(tmp_path, _production_config()) as client:
        write = client.put(
            "/api/keys/openai", json={"value": SECRET}, headers={"Origin": ALLOWED}
        )
        listing = client.get("/api/keys", headers={"Origin": ALLOWED})

    assert write.status_code == 403
    assert write.json()["detail"]["code"] == "credential_writes_disabled"
    assert listing.json()["writable"] is False


def test_production_allows_key_writes_when_explicitly_enabled(tmp_path) -> None:
    config = _production_config(allow_credential_writes=True)
    with _client(tmp_path, config) as client:
        write = client.put(
            "/api/keys/openai", json={"value": SECRET}, headers={"Origin": ALLOWED}
        )
        listing = client.get("/api/keys", headers={"Origin": ALLOWED})

    assert write.status_code == 200
    assert listing.json()["writable"] is True


def test_local_server_can_explicitly_disable_key_writes(tmp_path) -> None:
    config = Config(server=ServerConfig(allow_credential_writes=False))
    with _client(tmp_path, config) as client:
        write = client.put("/api/keys/openai", json={"value": SECRET})
        listing = client.get("/api/keys")

    assert write.status_code == 403
    assert listing.json()["writable"] is False


def test_credential_writes_can_be_configured_from_the_environment(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASEMODE_LOOM_ALLOW_CREDENTIAL_WRITES", "false")

    config = load_config()

    assert config.server.allow_credential_writes is False
    assert config.server.credential_writes_enabled() is False
