"""Observed per-model generation speed, averaged over timed nodes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config
from basemode_loom.store import GenerationStore


@pytest.fixture
def corpus(tmp_path) -> GenerationStore:
    return GenerationStore(tmp_path / "speed.sqlite")


def _client(store: GenerationStore) -> TestClient:
    return TestClient(create_app(store, Config()))


def _timed(store: GenerationStore, parent_id: str, text: str, model: str, timing: dict):
    node = store.add_child(
        parent_id,
        text,
        model=model,
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.update_metadata(node.id, {"timing": timing})
    return node


def test_speed_stats_average_timed_nodes_per_model(corpus) -> None:
    root = corpus.create_root("Once upon a time")
    _timed(
        corpus,
        root.id,
        " one.",
        "deepseek/deepseek-v4-flash",
        {
            "ttft_ms": 100.0,
            "elapsed_ms": 1000.0,
            "streaming_ms": 900.0,
            "completion_tokens": 20,
            "completion_tokens_per_second": 20.0,
        },
    )
    _timed(
        corpus,
        root.id,
        " two.",
        "deepseek/deepseek-v4-flash",
        {
            "ttft_ms": 300.0,
            "elapsed_ms": 2000.0,
            "streaming_ms": 1700.0,
            "completion_tokens": 30,
            "completion_tokens_per_second": 15.0,
        },
    )
    corpus.add_child(
        root.id, " untimed.", model="zai/glm-5.1", strategy="system",
        max_tokens=20, temperature=0.9,
    )

    stats = corpus.speed_stats_by_model()

    assert stats["deepseek/deepseek-v4-flash"] == {
        "timed_nodes": 2,
        "avg_ttft_ms": 200.0,
        "avg_elapsed_ms": 1500.0,
        "avg_completion_tokens_per_second": 17.5,
        "total_completion_tokens": 50,
    }
    assert "zai/glm-5.1" not in stats


def test_the_api_exposes_speed_by_model(corpus) -> None:
    root = corpus.create_root("Once upon a time")
    _timed(
        corpus,
        root.id,
        " one.",
        "deepseek/deepseek-v4-flash",
        {
            "ttft_ms": 100.0,
            "elapsed_ms": 1000.0,
            "streaming_ms": 900.0,
            "completion_tokens": 20,
            "completion_tokens_per_second": 20.0,
        },
    )

    with _client(corpus) as client:
        body = client.get("/api/models/speed").json()

    assert body["speed"]["deepseek/deepseek-v4-flash"]["timed_nodes"] == 1


def test_nothing_timed_is_an_empty_dict_not_an_error(corpus) -> None:
    root = corpus.create_root("Once upon a time")
    corpus.add_child(
        root.id, " untimed.", model="zai/glm-5.1", strategy="system",
        max_tokens=20, temperature=0.9,
    )

    with _client(corpus) as client:
        body = client.get("/api/models/speed").json()

    assert body["speed"] == {}
