"""End-to-end check that a starved reasoning-model stream surfaces its
finish_reason (from basemode's EmptyCompletionError) all the way through the
generate websocket, not just internally in LoomSession."""

from __future__ import annotations

from basemode.exceptions import EmptyCompletionError
from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def _recv_state(ws) -> dict:
    msg = ws.receive_json()
    assert msg["type"] == "state"
    return msg["state"]


def _init(ws, root_id: str) -> dict:
    ws.send_json({"type": "init", "root_id": root_id})
    return _recv_state(ws)


def test_generate_surfaces_finish_reason_on_empty_completion_error(
    tmp_path, monkeypatch
) -> None:
    async def failing_continue(prefix, model, **kwargs):
        raise EmptyCompletionError(
            model=model, strategy="system", finish_reason="length"
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("basemode_loom.session.continue_text", failing_continue)

    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = create_app(store)

    with TestClient(app) as client, client.websocket_connect("/ws/session") as ws:
        _init(ws, root.id)

        ws.send_json(
            {
                "type": "set_params",
                "model_plan": [
                    {
                        "model": "moonshot/kimi-k3",
                        "n_branches": 1,
                        "max_tokens": 20,
                        "temperature": 0.9,
                    }
                ],
                "persist": True,
            }
        )
        _recv_state(ws)

        ws.send_json({"type": "generate"})

        msg = ws.receive_json()
        assert msg["type"] == "generation_error"
        assert msg["category"] == "empty_response"
        assert msg["finish_reason"] == "length"
        assert msg["model"] == "moonshot/kimi-k3"
        assert msg["model_idx"] == 0
        assert msg["branch_idx"] == 0
        assert msg["slot_idx"] == 0
        assert "incident_id" in msg
