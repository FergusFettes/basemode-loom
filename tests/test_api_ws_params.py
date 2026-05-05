from __future__ import annotations

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


def _root_node(nodes: list[dict]) -> dict:
    for node in nodes:
        if node["parent_id"] is None:
            return node
    raise AssertionError("root node not found")


def test_set_params_persists_and_restores_on_reconnect(tmp_path) -> None:
    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            state = _init(ws, root.id)
            assert state["max_tokens"] == 200

            ws.send_json(
                {
                    "type": "set_params",
                    "model": "openai/gpt-4o-mini",
                    "max_tokens": 512,
                    "temperature": 0.4,
                    "n_branches": 3,
                    "context": "world facts",
                    "show_model_names": False,
                    "model_plan": [
                        {
                            "model": "openai/gpt-4o-mini",
                            "n_branches": 3,
                            "max_tokens": 512,
                            "temperature": 0.4,
                            "enabled": True,
                        }
                    ],
                    "persist": True,
                }
            )
            state = _recv_state(ws)
            assert state["max_tokens"] == 512
            assert state["temperature"] == 0.4
            assert state["n_branches"] == 3
            assert state["context"] == "world facts"
            assert state["show_model_names"] is False

        with client.websocket_connect("/ws/session") as ws:
            state = _init(ws, root.id)
            assert state["model"] == "openai/gpt-4o-mini"
            assert state["max_tokens"] == 512
            assert state["temperature"] == 0.4
            assert state["n_branches"] == 3
            assert state["context"] == "world facts"
            assert state["show_model_names"] is False

    persisted_root = store.root(root.id)
    tree = store.tree_for_node(root.id)
    context = store.get(persisted_root.context_id)
    assert context is not None
    assert context.text == "world facts"
    assert tree.show_model_names is False
    assert tree.model_plan == [
        {
            "model": "openai/gpt-4o-mini",
            "n_branches": 3,
            "max_tokens": 512,
            "temperature": 0.4,
            "enabled": True,
        }
    ]
    assert "model" not in persisted_root.metadata
    assert "max_tokens" not in persisted_root.metadata


def test_set_params_syncs_root_metadata_in_tree_endpoint(tmp_path) -> None:
    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, root.id)
            ws.send_json(
                {
                    "type": "set_params",
                    "model": "openai/gpt-4o",
                    "max_tokens": 600,
                    "temperature": 0.7,
                    "n_branches": 2,
                    "persist": True,
                }
            )
            _recv_state(ws)

        response = client.get(f"/api/roots/{root.id}/tree")
        assert response.status_code == 200
        body = response.json()
        root_node = _root_node(body["nodes"])
        assert body["tree"]["model_plan"] == [
            {
                "model": "openai/gpt-4o",
                "n_branches": 2,
                "max_tokens": 600,
                "temperature": 0.7,
                "enabled": True,
            }
        ]
        assert "model" not in root_node["metadata"]
        assert "max_tokens" not in root_node["metadata"]
        assert "temperature" not in root_node["metadata"]
        assert "n_branches" not in root_node["metadata"]


def test_set_params_rejects_invalid_values_with_field_errors(tmp_path) -> None:
    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed", metadata={"max_tokens": 200})
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, root.id)
            ws.send_json(
                {
                    "type": "set_params",
                    "temperature": 9,
                    "max_tokens": "a lot",
                    "persist": False,
                }
            )
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["message"] == "invalid set_params"
            assert msg["fields"]["temperature"] == "must be a number between 0 and 2"
            assert (
                msg["fields"]["max_tokens"] == "must be an integer between 50 and 8000"
            )
            assert msg["fields"]["persist"] == "only persist=true is supported"

    assert store.tree_for_node(root.id).model_plan[0]["max_tokens"] == 200


def test_set_params_last_write_wins_across_sessions(tmp_path) -> None:
    store = GenerationStore(tmp_path / "generations.sqlite")
    root = store.create_root("Seed")
    app = create_app(store)

    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/session") as ws_a,
            client.websocket_connect("/ws/session") as ws_b,
        ):
            _init(ws_a, root.id)
            _init(ws_b, root.id)

            ws_a.send_json({"type": "set_params", "max_tokens": 300, "persist": True})
            _recv_state(ws_a)

            ws_b.send_json(
                {
                    "type": "set_params",
                    "max_tokens": 700,
                    "temperature": 0.3,
                    "persist": True,
                }
            )
            _recv_state(ws_b)

        with client.websocket_connect("/ws/session") as ws:
            state = _init(ws, root.id)
            assert state["max_tokens"] == 700
            assert state["temperature"] == 0.3
