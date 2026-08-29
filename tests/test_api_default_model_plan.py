"""The default model plan a never-configured tree starts from.

The load-bearing property is that it lives in the corpus rather than in a
browser: two people pointed at the same server share one default, and either
can change it. Existing trees keep whatever plan they already have.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config
from basemode_loom.store import _DEFAULT_MODEL_PLAN, GenerationStore

ENDPOINT = "/api/settings/default-model-plan"


def _client(tmp_path) -> tuple[TestClient, GenerationStore]:
    store = GenerationStore(tmp_path / "defaults.sqlite")
    return TestClient(create_app(store, Config())), store


def test_defaults_to_the_built_in_plan(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        plan = client.get(ENDPOINT).json()["model_plan"]

    assert [entry["model"] for entry in plan] == [
        entry["model"] for entry in _DEFAULT_MODEL_PLAN
    ]


def test_stored_plan_survives_a_reopen_and_is_shared(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        written = client.put(
            ENDPOINT,
            json={"model_plan": [{"model": "zai/glm-5.1", "n_branches": 2}]},
        )
        assert written.status_code == 200
        assert [e["model"] for e in written.json()["model_plan"]] == ["zai/glm-5.1"]

    # A second client is a second browser: it must see the same default.
    reopened = GenerationStore(tmp_path / "defaults.sqlite")
    assert [e["model"] for e in reopened.default_model_plan()] == ["zai/glm-5.1"]
    assert reopened.default_model_plan()[0]["n_branches"] == 2


def test_null_clears_back_to_the_built_in_plan(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        client.put(ENDPOINT, json={"model_plan": [{"model": "zai/glm-5.1"}]})
        cleared = client.put(ENDPOINT, json={"model_plan": None}).json()

    assert [e["model"] for e in cleared["model_plan"]] == [
        e["model"] for e in _DEFAULT_MODEL_PLAN
    ]


def test_an_invalid_plan_is_refused_rather_than_coerced(tmp_path) -> None:
    client, store = _client(tmp_path)
    with client:
        response = client.put(
            ENDPOINT, json={"model_plan": [{"model": "zai/glm-5.1", "n_branches": 0}]}
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_model_plan"
    # And the stored default is untouched by the rejected write.
    assert store.default_model_plan() == _DEFAULT_MODEL_PLAN


def test_a_tree_with_its_own_plan_is_unaffected(tmp_path) -> None:
    client, store = _client(tmp_path)
    root, _ = store.save_continuations(
        "seed",
        [" one"],
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    tree = store.tree_for_node(root.id)
    store.update_tree_settings(tree.id, model_plan=[{"model": "openai/gpt-5.4"}])

    with client:
        client.put(ENDPOINT, json={"model_plan": [{"model": "zai/glm-5.1"}]})

    assert [e["model"] for e in store.get_tree(tree.id).model_plan] == [
        "openai/gpt-5.4"
    ]
