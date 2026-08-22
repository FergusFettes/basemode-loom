from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.types import Scope

from basemode_loom.api import StoreResolver
from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def test_store_resolver_scopes_rest_and_websocket_to_the_selected_store(
    tmp_path,
) -> None:
    first = GenerationStore(tmp_path / "first.sqlite")
    second = GenerationStore(tmp_path / "second.sqlite")
    first_root = first.create_root("first private tree")
    second_root = second.create_root("second private tree")

    def resolve(scope: Scope) -> GenerationStore:
        headers = dict(scope["headers"])
        return second if headers.get(b"x-test-store") == b"second" else first

    resolver: StoreResolver = resolve
    with TestClient(create_app(first, store_resolver=resolver)) as client:
        first_roots = client.get("/api/roots").json()
        second_roots = client.get(
            "/api/roots", headers={"x-test-store": "second"}
        ).json()

        with client.websocket_connect(
            "/ws/session", headers={"x-test-store": "second"}
        ) as websocket:
            websocket.send_json({"type": "init", "root_id": second_root.id})
            state = websocket.receive_json()
            websocket.send_json({"type": "init", "root_id": first_root.id})
            rejected = websocket.receive_json()

    assert [root["id"] for root in first_roots] == [first_root.id]
    assert [root["id"] for root in second_roots] == [second_root.id]
    assert state["type"] == "state"
    assert state["state"]["root_id"] == second_root.id
    assert rejected == {
        "type": "error",
        "message": f"unknown root_id: {first_root.id!r}",
    }
