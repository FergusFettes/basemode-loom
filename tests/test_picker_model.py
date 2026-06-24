"""Unit tests for the pure picker filter/sort/search model."""

from __future__ import annotations

from basemode_loom.store import Node
from basemode_loom.tui.widgets.picker_model import PickerModel, _TreeEntry


def _node(node_id: str, created_at: str) -> Node:
    return Node(
        id=node_id,
        parent_id=None,
        text=f"{node_id} text",
        model=None,
        strategy=None,
        max_tokens=None,
        temperature=None,
        created_at=created_at,
        metadata={},
        tree_id=node_id,
        kind="root",
        context_id=None,
        checked_out=False,
    )


def _entry(
    node_id: str,
    *,
    created_at: str = "2026-01-01T00:00:00Z",
    node_count: int = 1,
    name: str | None = None,
    category: str = "",
    domain: str = "",
    sources: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
) -> _TreeEntry:
    return _TreeEntry(
        root=_node(node_id, created_at),
        name=name,
        node_count=node_count,
        root_preview=f"{node_id} preview",
        leaf_preview="(at root)",
        category=category,
        domain=domain,
        sources=sources,
        models=models,
    )


def _model() -> PickerModel:
    model = PickerModel()
    model.set_entries(
        [
            _entry(
                "a",
                created_at="2026-01-01T00:00:00Z",
                node_count=2,
                name="alpha",
                category="code",
                domain="agent",
                sources=("codex",),
                models=("gpt-5",),
            ),
            _entry(
                "b",
                created_at="2026-03-01T00:00:00Z",
                node_count=5,
                name="beta",
                category="literature",
                domain="chat",
                sources=("claude",),
                models=("claude-opus",),
            ),
            _entry(
                "c",
                created_at="2026-02-01T00:00:00Z",
                node_count=3,
                name="gamma",
                category="code",
                domain="chat",
                sources=("codex", "claude"),
                models=("gpt-5", "claude-opus"),
            ),
        ]
    )
    return model


def _ids(entries: list[_TreeEntry]) -> list[str]:
    return [e.root.id for e in entries]


# --- sorting ---


def test_default_sort_recent():
    model = _model()
    assert model.sort_mode == "recent"
    assert _ids(model.visible()) == ["b", "c", "a"]


def test_cycle_sort_to_oldest():
    model = _model()
    assert model.cycle_sort() == "oldest"
    assert _ids(model.visible()) == ["a", "c", "b"]


def test_cycle_sort_to_nodes():
    model = _model()
    while model.cycle_sort() != "nodes":
        pass
    assert _ids(model.visible()) == ["b", "c", "a"]  # 5, 3, 2


def test_cycle_sort_to_name():
    model = _model()
    while model.cycle_sort() != "name":
        pass
    assert _ids(model.visible()) == ["a", "b", "c"]  # alpha, beta, gamma


# --- facet filtering ---


def test_facet_filter_single_value():
    model = _model()
    model.toggle_facet("category", "code")
    assert set(_ids(model.visible())) == {"a", "c"}


def test_facet_or_within_facet():
    model = _model()
    model.toggle_facet("category", "code")
    model.toggle_facet("category", "literature")
    assert set(_ids(model.visible())) == {"a", "b", "c"}


def test_facet_and_across_facets():
    model = _model()
    model.toggle_facet("category", "code")  # a, c
    model.toggle_facet("domain", "chat")  # b, c
    assert _ids(model.visible()) == ["c"]


def test_multi_valued_source_facet_matches_any():
    model = _model()
    model.toggle_facet("source", "claude")  # b and c (c has both)
    assert set(_ids(model.visible())) == {"b", "c"}


def test_toggle_facet_off_restores():
    model = _model()
    model.toggle_facet("category", "code")
    model.toggle_facet("category", "code")
    assert len(model.visible()) == 3
    assert "category" not in model.active


def test_set_facet_replaces_values():
    model = _model()
    model.set_facet("category", {"literature"})
    assert _ids(model.visible()) == ["b"]
    model.set_facet("category", set())
    assert len(model.visible()) == 3


# --- text filter ---


def test_text_filter_matches_name():
    model = _model()
    model.set_text_filter("beta")
    assert _ids(model.visible()) == ["b"]


def test_text_filter_matches_model():
    model = _model()
    model.set_text_filter("gpt-5")
    assert set(_ids(model.visible())) == {"a", "c"}


def test_text_filter_matches_category():
    model = _model()
    model.set_text_filter("literature")
    assert _ids(model.visible()) == ["b"]


# --- facet values / counts ---


def test_facet_values_counts_sorted():
    model = _model()
    assert model.facet_values("category") == [("code", 2), ("literature", 1)]
    assert model.facet_values("source") == [("claude", 2), ("codex", 2)]


def test_has_facet_values():
    model = _model()
    assert model.has_facet_values("category")
    model.set_entries([_entry("x")])
    assert not model.has_facet_values("category")


# --- search / relevance ---


def test_query_restricts_and_orders_by_ranking():
    model = _model()
    model.set_query("anything", {"c": 0.9, "a": 0.3})
    assert model.sort_mode == "relevance"
    assert _ids(model.visible()) == ["c", "a"]  # b excluded, ranked order


def test_clear_query_restores_sort():
    model = _model()
    model.set_query("anything", {"c": 0.9})
    model.clear_query()
    assert model.sort_mode == "recent"
    assert len(model.visible()) == 3


def test_facets_apply_within_search_results():
    model = _model()
    model.toggle_facet("category", "code")  # a, c
    model.set_query("anything", {"a": 0.5, "b": 0.9, "c": 0.1})
    # ranking has a,b,c; facet keeps a,c; ordered by score -> a then c
    assert _ids(model.visible()) == ["a", "c"]


# --- clear all / status ---


def test_clear_all_resets_everything():
    model = _model()
    model.toggle_facet("category", "code")
    model.set_text_filter("alpha")
    model.set_query("q", {"a": 1.0})
    assert model.filters_active
    model.clear_all()
    assert not model.filters_active
    assert model.sort_mode == "recent"
    assert len(model.visible()) == 3


def test_total_count():
    model = _model()
    assert model.total_count == 3
