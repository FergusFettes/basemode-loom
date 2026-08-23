"""One connection may have several generations in flight at once.

Generating in one place in the tree used to lock the whole connection until
that job finished ("generation already in progress"), which made it
impossible to start a second continuation elsewhere while a slow provider was
still streaming.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import ServerConfig, load_config
from basemode_loom.store import GenerationStore


def _recv_state(ws) -> dict:
    msg = ws.receive_json()
    assert msg["type"] == "state"
    return msg["state"]


def _init(ws, root_id: str) -> dict:
    ws.send_json({"type": "init", "root_id": root_id})
    return _recv_state(ws)


def _drain_until(ws, wanted: str, limit: int = 200) -> list[dict]:
    seen: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == wanted:
            return seen
    raise AssertionError(f"never saw {wanted!r}; saw {[m['type'] for m in seen]}")


def _app_with(store: GenerationStore, server: ServerConfig):
    config = load_config()
    config.server = server
    return create_app(store, config=config)


def test_a_second_generation_runs_alongside_the_first(tmp_path, monkeypatch) -> None:
    release = asyncio.Event()

    async def slow_continue(prefix, model, **kwargs):
        if "slow" in model:
            await release.wait()
        yield f"from {model}"

    monkeypatch.setattr("basemode_loom.session.continue_text", slow_continue)

    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = create_app(store)

    with TestClient(app) as client, client.websocket_connect("/ws/session") as ws:
        _init(ws, root.id)
        ws.send_json({"type": "set_params", "model": "slow-model", "n_branches": 1})
        _recv_state(ws)
        ws.send_json({"type": "generate"})

        # Second job, a different model, while the first is still blocked.
        ws.send_json({"type": "set_params", "model": "quick-model", "n_branches": 1})
        _recv_state(ws)
        ws.send_json({"type": "generate"})

        messages = _drain_until(ws, "generation_complete")
        assert not [m for m in messages if m["type"] == "generation_busy"]
        completed = [m for m in messages if m["type"] == "generation_complete"]
        assert "quick-model" in completed[0]["new_nodes"][0]["model"]

        release.set()
        _drain_until(ws, "generation_complete")

    assert sorted(node.model for node in store.children(root.id)) == [
        "quick-model",
        "slow-model",
    ]


def test_the_per_session_cap_is_reported(tmp_path, monkeypatch) -> None:
    release = asyncio.Event()

    async def blocked_continue(prefix, model, **kwargs):
        await release.wait()
        yield "done"

    monkeypatch.setattr("basemode_loom.session.continue_text", blocked_continue)

    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = _app_with(store, ServerConfig(concurrent_generations_per_session=2))

    with TestClient(app) as client, client.websocket_connect("/ws/session") as ws:
        _init(ws, root.id)
        for _ in range(2):
            ws.send_json({"type": "generate"})
        ws.send_json({"type": "generate"})

        busy = _drain_until(ws, "generation_busy")[-1]
        assert "2 generations already running" in busy["message"]

        release.set()
        ws.send_json({"type": "cancel"})


def test_timeout_is_reported_as_a_failure_for_each_unfinished_model(tmp_path, monkeypatch) -> None:
    async def blocked_continue(*_args, **_kwargs):
        await asyncio.Future()
        yield "unreachable"

    monkeypatch.setattr("basemode_loom.session.continue_text", blocked_continue)

    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = _app_with(store, ServerConfig(generation_timeout_seconds=0.01))

    with TestClient(app) as client, client.websocket_connect("/ws/session") as ws:
        _init(ws, root.id)
        ws.send_json({"type": "set_params", "model": "slow-model", "n_branches": 1})
        _recv_state(ws)
        ws.send_json({"type": "generate"})

        timeout = _drain_until(ws, "generation_error")[-1]
        assert timeout == {
            "type": "generation_error",
            "error": "generation timed out",
            "model": "slow-model",
            "model_idx": 0,
            "branch_idx": 0,
            "category": "timeout",
        }
