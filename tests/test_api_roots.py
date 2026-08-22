from __future__ import annotations

from fastapi.testclient import TestClient

from basemode_loom.api import _rest
from basemode_loom.api.app import create_app
from basemode_loom.images import ImageGenerationError
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


def test_generate_root_image_returns_base64_and_prompt(tmp_path, monkeypatch) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")
    root = store.create_root("a story about a cat")

    monkeypatch.setattr(
        _rest, "generate_branch_image", lambda prompt: ("ZmFrZQ==", "image/png")
    )

    with TestClient(create_app(store)) as client:
        response = client.post(f"/api/roots/{root.id}/image")

    assert response.status_code == 200
    body = response.json()
    assert body["image_base64"] == "ZmFrZQ=="
    assert body["mime_type"] == "image/png"
    assert body["prompt"] == "a story about a cat"


def test_generate_root_image_404_for_unknown_root(tmp_path) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")

    with TestClient(create_app(store)) as client:
        response = client.post("/api/roots/missing/image")

    assert response.status_code == 404


def test_generate_root_image_400_on_generation_failure(tmp_path, monkeypatch) -> None:
    store = GenerationStore(tmp_path / "roots.sqlite")
    root = store.create_root("a story about a cat")

    def failing(prompt: str) -> tuple[str, str]:
        raise ImageGenerationError("no OpenAI API key configured")

    monkeypatch.setattr(_rest, "generate_branch_image", failing)

    with TestClient(create_app(store)) as client:
        response = client.post(f"/api/roots/{root.id}/image")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "image_generation_failed"
