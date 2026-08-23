from __future__ import annotations

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def _recv_state(ws) -> dict:
    msg = ws.receive_json()
    assert msg["type"] == "state", msg
    return msg["state"]


def _init(ws, root_id: str) -> dict:
    ws.send_json({"type": "init", "root_id": root_id})
    return _recv_state(ws)


def _seed(tmp_path):
    store = GenerationStore(tmp_path / "generations.sqlite")
    root, children = store.save_continuations(
        "Hello",
        [" world"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    return store, root, children[0]


def test_add_node_hangs_a_manual_child_off_the_parent(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "add_node", "parent_id": child.id, "text": " again"})
            state = _recv_state(ws)

    assert state["current_node"]["parent_id"] == child.id
    assert state["current_node"]["text"] == " again"
    assert state["current_node"]["model"] == "manual"
    assert state["full_text"] == "Hello world again"


def test_add_node_rejects_an_unknown_parent(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "add_node", "parent_id": "nope", "text": "x"})
            msg = ws.receive_json()

    assert msg["type"] == "error"
    assert "nope" in msg["message"]


def test_edit_node_rewrites_a_single_segment(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "edit_node", "node_id": child.id, "text": " earth"})
            state = _recv_state(ws)

    assert state["current_node"]["text"] == " earth"
    assert state["full_text"] == "Hello earth"


def test_edit_node_with_unchanged_text_is_a_no_op(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "edit_node", "node_id": child.id, "text": " world"})
            state = _recv_state(ws)

    assert state["current_node_id"] == child.id
    assert state["full_text"] == "Hello world"


def test_edit_node_requires_a_node_id(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "edit_node", "text": "x"})
            msg = ws.receive_json()

    assert msg["type"] == "error"
