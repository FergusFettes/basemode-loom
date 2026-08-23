"""Flagging a generation as bad, and reading the flagged ones back.

The flag is a bare boolean on purpose: the node and its parent already hold
everything needed to diagnose the failure, so the flag only marks which
generations are worth reading.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config
from basemode_loom.session import LoomSession
from basemode_loom.store import GenerationStore


@pytest.fixture
def corpus(tmp_path) -> GenerationStore:
    return GenerationStore(tmp_path / "flags.sqlite")


def _client(store: GenerationStore) -> TestClient:
    return TestClient(create_app(store, Config()))


def _add(store: GenerationStore, parent_id: str, text: str, model: str):
    return store.add_child(
        parent_id,
        text,
        model=model,
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )


def _seeded(store: GenerationStore):
    parent, children = store.save_continuations(
        "The ship rounded the headland and",
        [" the sea opened out.", "the sea opened out."],
        model="deepseek/deepseek-v4-flash",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    return parent, children


def test_flagging_a_node_is_a_toggle(corpus) -> None:
    parent, children = _seeded(corpus)
    session = LoomSession(corpus, parent.id)

    assert session.toggle_node_flag(children[1].id) is True
    assert corpus.get(children[1].id).metadata["flagged"] is True

    assert session.toggle_node_flag(children[1].id) is False
    assert corpus.get(children[1].id).metadata["flagged"] is False


def test_flagging_an_unknown_node_reports_rather_than_raises(corpus) -> None:
    parent, _ = _seeded(corpus)
    session = LoomSession(corpus, parent.id)

    assert session.toggle_node_flag("nope") is None


def test_flagging_leaves_other_metadata_alone(corpus) -> None:
    parent, children = _seeded(corpus)
    session = LoomSession(corpus, parent.id)
    corpus.update_metadata(children[0].id, {"bookmarked": True})

    session.toggle_node_flag(children[0].id)

    metadata = corpus.get(children[0].id).metadata
    assert metadata["bookmarked"] is True
    assert metadata["flagged"] is True


def test_only_flagged_nodes_come_back(corpus) -> None:
    _parent, children = _seeded(corpus)
    corpus.update_metadata(children[1].id, {"flagged": True})
    corpus.update_metadata(children[0].id, {"flagged": False})

    flagged = corpus.flagged_nodes()

    assert [node.id for node in flagged] == [children[1].id]


def test_boundary_correction_enters_the_flagged_generation_corpus(corpus) -> None:
    parent, children = _seeded(corpus)
    session = LoomSession(corpus, parent.id)

    corrected = session.remove_leading_space(children[0].id)

    assert corrected is not None
    assert corrected.metadata["flagged"] is True
    # Corrections from before automatic flagging still belong in the same
    # review corpus; an explicit flag toggle must not hide that evidence.
    corpus.update_metadata(corrected.id, {"flagged": False})
    assert [node.id for node in corpus.flagged_nodes()] == [children[0].id]
    with _client(corpus) as client:
        body = client.get("/api/flags").json()
    assert body["flags"][0]["in_place_edits"] == [
        {"kind": "remove_leading_space", "before": " the sea opened out.", "after": "the sea opened out."}
    ]
    assert body["by_model"]["deepseek/deepseek-v4-flash"]["flagged"] == 1


def test_flagged_nodes_can_be_narrowed_to_one_model(corpus) -> None:
    parent, children = _seeded(corpus)
    corpus.update_metadata(children[0].id, {"flagged": True})
    other = _add(corpus, parent.id, " elsewhere.", "zai/glm-5.1")
    corpus.update_metadata(other.id, {"flagged": True})

    assert [n.id for n in corpus.flagged_nodes(model="zai/glm-5.1")] == [other.id]
    assert len(corpus.flagged_nodes()) == 2


def test_flag_counts_weigh_flags_against_use(corpus) -> None:
    parent, children = _seeded(corpus)
    corpus.update_metadata(children[0].id, {"flagged": True})
    _add(corpus, parent.id, " elsewhere.", "zai/glm-5.1")

    counts = corpus.flag_counts_by_model()

    assert counts["deepseek/deepseek-v4-flash"] == {"generated": 2, "flagged": 1}
    assert counts["zai/glm-5.1"] == {"generated": 1, "flagged": 0}


def test_the_api_pairs_each_flag_with_what_the_model_was_given(corpus) -> None:
    _parent, children = _seeded(corpus)
    corpus.update_metadata(children[1].id, {"flagged": True})

    with _client(corpus) as client:
        body = client.get("/api/flags").json()

    entry = body["flags"][0]
    assert entry["node_id"] == children[1].id
    assert entry["prefix"].endswith("The ship rounded the headland and")
    assert entry["text"] == "the sea opened out."
    assert entry["model"] == "deepseek/deepseek-v4-flash"
    assert entry["strategy"] == "system"
    assert entry["prefix_truncated"] is False
    assert body["by_model"]["deepseek/deepseek-v4-flash"]["flagged"] == 1


def test_the_api_can_narrow_to_one_model(corpus) -> None:
    parent, children = _seeded(corpus)
    corpus.update_metadata(children[0].id, {"flagged": True})
    other = _add(corpus, parent.id, " elsewhere.", "zai/glm-5.1")
    corpus.update_metadata(other.id, {"flagged": True})

    with _client(corpus) as client:
        body = client.get("/api/flags", params={"model": "zai/glm-5.1"}).json()

    assert [entry["node_id"] for entry in body["flags"]] == [other.id]


def test_a_long_prefix_is_trimmed_to_the_seam(corpus) -> None:
    root = corpus.create_root("x" * 5000)
    child = _add(corpus, root.id, " continued", "m")
    corpus.update_metadata(child.id, {"flagged": True})

    with _client(corpus) as client:
        entry = client.get("/api/flags", params={"prefix_chars": 100}).json()["flags"][
            0
        ]

    assert len(entry["prefix"]) == 100
    assert entry["prefix_truncated"] is True


def test_nothing_flagged_is_an_empty_list_not_an_error(corpus) -> None:
    _seeded(corpus)

    with _client(corpus) as client:
        body = client.get("/api/flags").json()

    assert body["flags"] == []
