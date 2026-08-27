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
    assert item["updated_at"]
    assert item["archived"] is False
    assert item["breadth"] == 1
    assert item["avg_branching_factor"] == 1.0
    assert item["branchiness"] == 0.0
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
        "direction",
        "archived",
        "offset",
        "limit",
    }
    response = operation["responses"]["200"]["content"]["application/json"]
    assert response["schema"]["$ref"].endswith("/TreeCatalogResponse")
    properties = schema["components"]["schemas"]["TreeSummary"]["properties"]
    assert properties.keys() >= {
        "updated_at",
        "archived",
        "node_count",
        "breadth",
        "avg_branching_factor",
        "branchiness",
    }


def test_tree_catalog_archive_selector_and_recent_updated_sort(tmp_path) -> None:
    store, alpha, beta = _catalog_store(tmp_path)
    store.set_tree_archived(beta[0].tree_id, True)
    with closing(store.connect()) as conn, conn:
        conn.execute(
            "UPDATE trees SET updated_at = '2099-01-01T00:00:00' WHERE id = ?",
            (alpha[0].tree_id,),
        )

    with TestClient(create_app(store)) as client:
        active = client.get("/api/trees")
        archived = client.get("/api/trees?archived=archived")
        both = client.get("/api/trees?archived=both&sort=recent")

    assert [item["id"] for item in active.json()["items"]] == [alpha[0].id]
    assert [item["id"] for item in archived.json()["items"]] == [beta[0].id]
    assert both.json()["items"][0]["id"] == alpha[0].id


def test_tree_catalog_shape_sorts_in_both_directions(tmp_path) -> None:
    store = GenerationStore(tmp_path / "shapes.sqlite")
    chain = store.create_root("chain")
    parent = chain
    for index in range(3):
        parent = store.add_child(
            parent.id, f" chain {index}", model="m", strategy="s",
            max_tokens=1, temperature=0.0,
        )
    wide = store.create_root("wide")
    for index in range(3):
        store.add_child(
            wide.id, f" wide {index}", model="m", strategy="s",
            max_tokens=1, temperature=0.0,
        )

    with TestClient(create_app(store)) as client:
        descending = client.get("/api/trees?sort=branching&direction=desc").json()
        ascending = client.get("/api/trees?sort=breadth&direction=asc").json()

    assert descending["items"][0]["id"] == wide.id
    assert descending["items"][0]["avg_branching_factor"] == 3.0
    assert descending["items"][0]["branchiness"] == 1.0
    assert ascending["items"][0]["id"] == chain.id
    assert ascending["items"][0]["breadth"] == 1


def test_tree_catalog_query_count_is_constant_across_page_sizes(
    tmp_path, monkeypatch
) -> None:
    store, _alpha, _beta = _catalog_store(tmp_path)
    original_connect = store.connect
    select_counts: list[int] = []

    def traced_connect():
        conn = original_connect()
        conn.set_trace_callback(
            lambda sql: select_counts.append(1)
            if sql.lstrip().upper().startswith(("SELECT", "WITH"))
            else None
        )
        return conn

    monkeypatch.setattr(store, "connect", traced_connect)
    with TestClient(create_app(store)) as client:
        client.get("/api/trees?limit=1")
        one = len(select_counts)
        select_counts.clear()
        client.get("/api/trees?limit=200")
        many = len(select_counts)

    assert one == many
    assert many <= 4
