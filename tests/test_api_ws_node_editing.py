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


def test_remove_leading_space_updates_the_existing_node_in_place(tmp_path) -> None:
    store, root, child = _seed(tmp_path)
    grandchild = store.add_child(
        child.id, " again", model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "remove_leading_space", "node_id": child.id})
            state = _recv_state(ws)

    assert state["current_node_id"] == child.id
    assert state["current_node"]["text"] == "world"
    assert state["full_text"] == "Helloworld"
    assert store.get(grandchild.id) is not None
    assert [node.id for node in store.children(root.id)] == [child.id]
    assert store.get(child.id).metadata["in_place_edits"] == [
        {"kind": "remove_leading_space", "before": " world", "after": "world"}
    ]
    assert store.get(child.id).metadata["flagged"] is True


def test_add_leading_space_updates_the_existing_node_in_place(tmp_path) -> None:
    store, root, child = _seed(tmp_path)
    store.update_text(child.id, "world")
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "add_leading_space", "node_id": child.id})
            state = _recv_state(ws)

    assert state["current_node"]["text"] == " world"
    assert [node.id for node in store.children(root.id)] == [child.id]
    assert store.get(child.id).metadata["in_place_edits"] == [
        {"kind": "add_leading_space", "before": "world", "after": " world"}
    ]
    assert store.get(child.id).metadata["flagged"] is True


def test_edit_node_requires_a_node_id(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "edit_node", "text": "x"})
            msg = ws.receive_json()

    assert msg["type"] == "error"


def test_add_node_restores_the_space_the_user_left_out(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "add_node", "parent_id": child.id, "text": "again"})
            state = _recv_state(ws)

    assert state["current_node"]["text"] == " again"
    assert state["full_text"] == "Hello world again"


def test_add_node_leaves_a_deliberate_boundary_alone(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            # Punctuation and a newline are joins the writer meant.
            ws.send_json({"type": "add_node", "parent_id": child.id, "text": "!"})
            first = _recv_state(ws)
            ws.send_json({"type": "add_node", "parent_id": child.id, "text": "\nNext"})
            second = _recv_state(ws)
            ws.send_json({"type": "add_node", "parent_id": child.id, "text": "'s end"})
            third = _recv_state(ws)

    assert first["full_text"] == "Hello world!"
    assert second["full_text"] == "Hello world\nNext"
    assert third["full_text"] == "Hello world's end"


def test_edit_node_restores_the_space_the_user_left_out(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "edit_node", "node_id": child.id, "text": "earth"})
            state = _recv_state(ws)

    assert state["current_node"]["text"] == " earth"
    assert state["full_text"] == "Hello earth"


def test_delete_node_takes_its_subtree_and_lands_on_the_parent(tmp_path) -> None:
    store, root, child = _seed(tmp_path)
    grandchild = store.add_child(
        child.id, " again", model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, grandchild.id)
            ws.send_json({"type": "delete_node", "node_id": child.id})
            state = _recv_state(ws)

    assert state["current_node_id"] == root.id
    assert store.get(child.id) is None
    assert store.get(grandchild.id) is None


def test_delete_node_refuses_the_root(tmp_path) -> None:
    store, root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "delete_node", "node_id": root.id})
            msg = ws.receive_json()

    assert msg["type"] == "error"
    assert store.get(root.id) is not None


def test_bookmark_node_toggles_any_node_not_just_the_current_one(tmp_path) -> None:
    store, root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "bookmark_node", "node_id": root.id})
            _recv_state(ws)
            assert store.get(root.id).metadata.get("bookmarked") is True

            ws.send_json({"type": "bookmark_node", "node_id": root.id})
            _recv_state(ws)
            assert store.get(root.id).metadata.get("bookmarked") is False


def test_bookmark_node_rejects_an_unknown_node(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "bookmark_node", "node_id": "nope"})
            msg = ws.receive_json()

    assert msg["type"] == "error"


def test_flag_node_marks_a_generation_as_bad(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "flag_node", "node_id": child.id})
            _recv_state(ws)
            assert store.get(child.id).metadata.get("flagged") is True

            ws.send_json({"type": "flag_node", "node_id": child.id})
            _recv_state(ws)
            assert store.get(child.id).metadata.get("flagged") is False


def test_flag_node_rejects_an_unknown_node(tmp_path) -> None:
    store, _root, child = _seed(tmp_path)
    app = create_app(store)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            _init(ws, child.id)
            ws.send_json({"type": "flag_node", "node_id": "nope"})
            msg = ws.receive_json()

    assert msg["type"] == "error"
