from __future__ import annotations

from contextlib import closing

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def _catalog_store(tmp_path):
    store = GenerationStore(tmp_path / "catalog.sqlite")
    alpha_root, alpha_children = store.save_continuations(
        "alpha root",
        [" feline continuation"],
        model="openai/gpt-5",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
        metadata={"source": "codex"},
    )
    store.update_tree_settings(
        alpha_root.tree_id,
        name="Alpha",
        metadata={"category": "code", "domain": "agents"},
    )
    beta_root, beta_children = store.save_continuations(
        "beta root",
        [" canine continuation"],
        model="anthropic/claude-opus",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
        metadata={"source": "claude"},
    )
    store.update_tree_settings(
        beta_root.tree_id,
        name="Beta",
        metadata={"category": "writing", "domain": "chat"},
    )
    return store, (alpha_root, alpha_children[0]), (beta_root, beta_children[0])


def test_tree_catalog_returns_picker_summaries_and_facets(tmp_path) -> None:
    store, alpha, _beta = _catalog_store(tmp_path)

    with TestClient(create_app(store)) as client:
        response = client.get("/api/trees")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    item = next(item for item in body["items"] if item["id"] == alpha[0].id)
    assert item["name"] == "Alpha"
    assert item["node_count"] == 2
    assert item["category"] == "code"
    assert item["domain"] == "agents"
    assert item["sources"] == ["codex"]
    assert item["models"] == ["gpt-5"]
    assert body["facets"]["category"] == [
        {"value": "code", "count": 1},
        {"value": "writing", "count": 1},
    ]
    assert body["search"]["metadata"] is True


def test_tree_catalog_filters_facets_with_or_and_semantics(tmp_path) -> None:
    store, alpha, beta = _catalog_store(tmp_path)

    with TestClient(create_app(store)) as client:
        both = client.get("/api/trees?category=code&category=writing")
        one = client.get("/api/trees?category=code&domain=agents")
        none = client.get("/api/trees?category=code&domain=chat")

    assert {item["id"] for item in both.json()["items"]} == {
        alpha[0].id,
        beta[0].id,
    }
    assert [item["id"] for item in one.json()["items"]] == [alpha[0].id]
    assert none.json()["total"] == 0


def test_tree_catalog_uses_metadata_query_without_indexes(tmp_path) -> None:
    store, alpha, _beta = _catalog_store(tmp_path)

    with TestClient(create_app(store)) as client:
        response = client.get("/api/trees", params={"q": "agents"})

    body = response.json()
    assert [item["id"] for item in body["items"]] == [alpha[0].id]
    assert body["items"][0]["score"] is None
    assert body["search"]["keyword"] is False


def test_tree_catalog_exposes_ranked_keyword_search(tmp_path) -> None:
    store, alpha, beta = _catalog_store(tmp_path)
    with closing(store.connect()) as conn, conn:
        conn.execute(
            "CREATE VIRTUAL TABLE nodes_fts USING fts5(node_id UNINDEXED, text)"
        )
        for node in (*alpha, *beta):
            conn.execute(
                "INSERT INTO nodes_fts(node_id, text) VALUES (?, ?)",
                (node.id, node.text),
            )

    with TestClient(create_app(store)) as client:
        response = client.get("/api/trees", params={"q": "feline"})

    body = response.json()
    assert body["search"]["keyword"] is True
    assert [item["id"] for item in body["items"]] == [alpha[0].id]
    assert body["items"][0]["score"] is not None
    assert body["items"][0]["best_node_id"] == alpha[1].id


def test_tree_catalog_schema_is_published_in_openapi(tmp_path) -> None:
    store, _alpha, _beta = _catalog_store(tmp_path)
    schema = create_app(store).openapi()
    operation = schema["paths"]["/api/trees"]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} >= {
        "q",
        "category",
        "domain",
        "source",
        "model",
        "sort",
        "offset",
        "limit",
    }
    response = operation["responses"]["200"]["content"]["application/json"]
    assert response["schema"]["$ref"].endswith("/TreeCatalogResponse")
