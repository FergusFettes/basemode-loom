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
