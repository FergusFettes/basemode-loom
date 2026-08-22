from __future__ import annotations

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def test_roots_archive_and_restore_via_patch(tmp_path) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")
    root = store.create_root("root text")

    with TestClient(create_app(store)) as client:
        active = client.get("/api/roots").json()
        assert [r["id"] for r in active] == [root.id]
        assert active[0]["archived"] is False

        archived = client.patch(f"/api/roots/{root.id}", json={"archived": True})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True

        assert client.get("/api/roots").json() == []
        shown = client.get("/api/roots?archived=true").json()
        assert [r["id"] for r in shown] == [root.id]
        assert shown[0]["archived"] is True

        restored = client.patch(f"/api/roots/{root.id}", json={"archived": False})
        assert restored.status_code == 200
        assert restored.json()["archived"] is False
        assert [r["id"] for r in client.get("/api/roots").json()] == [root.id]


def test_patch_unknown_root_returns_404(tmp_path) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")

    with TestClient(create_app(store)) as client:
        response = client.patch("/api/roots/missing", json={"archived": True})

    assert response.status_code == 404


def test_node_shape_endpoint_returns_topology_metrics(tmp_path) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")
    root = store.create_root("root")
    a = store.add_child(
        root.id, " a", model="gpt-4o-mini", strategy="s", max_tokens=5, temperature=0.7
    )
    store.add_child(
        a.id, " a1", model="gpt-4o-mini", strategy="s", max_tokens=5, temperature=0.7
    )
    store.add_child(
        a.id, " a2", model="gpt-4o-mini", strategy="s", max_tokens=5, temperature=0.7
    )

    with TestClient(create_app(store)) as client:
        response = client.get(f"/api/nodes/{a.id}/shape")

    assert response.status_code == 200
    body = response.json()
    assert body["node_id"] == a.id
    assert body["subtree_size"] == 3
    assert body["descendant_count"] == 2
    assert body["child_count"] == 2
    assert body["branchiness"] == 1.0


def test_node_shape_endpoint_404_for_unknown_node(tmp_path) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")

    with TestClient(create_app(store)) as client:
        response = client.get("/api/nodes/missing/shape")

    assert response.status_code == 404
