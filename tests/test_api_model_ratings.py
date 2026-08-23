"""Per-user model thumbs over the API.

The load-bearing properties: a thumb is stored under the same normalized
model ID generation uses, it lands in basemode's config file rather than in
the corpus database (so it survives a `--db` swap), and a shared deployment
does not let visitors rewrite the operator's preferences.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config, ServerConfig
from basemode_loom.store import GenerationStore

ALLOWED = "https://grove.example.com"


@pytest.fixture(autouse=True)
def isolated_key_store(tmp_path, monkeypatch):
    """basemode.keys resolves its paths at import time, so patch the globals."""
    auth_file = tmp_path / "auth.json"
    monkeypatch.setattr("basemode.keys._CONFIG_DIR", tmp_path)
    monkeypatch.setattr("basemode.keys._AUTH_FILE", auth_file)
    return auth_file


def _client(tmp_path, config: Config | None = None) -> TestClient:
    store = GenerationStore(tmp_path / "ratings.sqlite")
    return TestClient(create_app(store, config or Config()))


def test_ratings_start_empty_and_writable(tmp_path) -> None:
    with _client(tmp_path) as client:
        body = client.get("/api/models/ratings").json()

    assert body == {"ratings": {}, "writable": True}


def test_a_thumb_is_stored_under_the_resolved_model_id(tmp_path, isolated_key_store):
    with _client(tmp_path) as client:
        response = client.put(
            "/api/models/rating", json={"model": "gpt-4o-mini", "rating": 1}
        )

        assert response.status_code == 200
        assert response.json() == {"model": "openai/gpt-4o-mini", "rating": 1}
        assert client.get("/api/models/ratings").json()["ratings"] == {
            "openai/gpt-4o-mini": 1
        }

    stored = json.loads(isolated_key_store.read_text())
    assert stored["model_ratings"] == {"openai/gpt-4o-mini": 1}


def test_reading_one_rating_normalizes_the_model_id(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/api/models/rating", json={"model": "openai/gpt-4o-mini", "rating": -1}
        )

        response = client.get("/api/models/rating", params={"model": "gpt-4o-mini"})

    assert response.status_code == 200
    assert response.json() == {"model": "gpt-4o-mini", "rating": -1}


def test_an_unrated_model_reads_back_as_null(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/models/rating", params={"model": "gpt-4o-mini"})

    assert response.json() == {"model": "gpt-4o-mini", "rating": None}


def test_a_null_rating_clears_the_thumb(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.put("/api/models/rating", json={"model": "gpt-4o-mini", "rating": 1})

        response = client.put(
            "/api/models/rating", json={"model": "gpt-4o-mini", "rating": None}
        )

        assert response.json() == {"model": "openai/gpt-4o-mini", "rating": None}
        assert client.get("/api/models/ratings").json()["ratings"] == {}


@pytest.mark.parametrize("rating", [0, 2, -5, "up", True])
def test_only_thumbs_are_accepted(tmp_path, rating) -> None:
    with _client(tmp_path) as client:
        response = client.put(
            "/api/models/rating", json={"model": "gpt-4o-mini", "rating": rating}
        )

    assert response.status_code == 422


def test_a_blank_model_is_rejected(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/models/rating", json={"model": "  ", "rating": 1})

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "empty_model"}


def test_production_refuses_writes_and_says_so_in_the_listing(tmp_path) -> None:
    config = Config(server=ServerConfig(production=True, allowed_origins=[ALLOWED]))
    with _client(tmp_path, config) as client:
        response = client.put(
            "/api/models/rating",
            json={"model": "gpt-4o-mini", "rating": 1},
            headers={"Origin": ALLOWED},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == {"code": "rating_writes_disabled"}
        listed = client.get("/api/models/ratings", headers={"Origin": ALLOWED})

    assert listed.json()["writable"] is False


def test_production_can_opt_back_into_writes(tmp_path) -> None:
    config = Config(
        server=ServerConfig(
            production=True, allowed_origins=[ALLOWED], allow_rating_writes=True
        )
    )
    with _client(tmp_path, config) as client:
        response = client.put(
            "/api/models/rating",
            json={"model": "gpt-4o-mini", "rating": 1},
            headers={"Origin": ALLOWED},
        )

    assert response.status_code == 200


def test_rated_models_surface_in_the_model_listing(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.put("/api/models/rating", json={"model": "gpt-4o-mini", "rating": 1})

        models = client.get(
            "/api/models", params={"search": "gpt-4o-mini", "available": False}
        ).json()["models"]

    rated = [m for m in models if m["model"].endswith("gpt-4o-mini")]
    assert rated
    assert rated[0]["rating"] == 1
