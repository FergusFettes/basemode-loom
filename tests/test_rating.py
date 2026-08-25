import json

import pytest

from basemode_loom.rating import (
    TIE,
    batches_from_store,
    bootstrap_ratings,
    comparisons_from_batches,
    deliberation_seconds,
    fit_davidson,
    graph_summary,
    naming,
    pair_uncertainty,
    recommend_cohort,
)
from basemode_loom.rating.design import information_matrix, total_variance
from basemode_loom.rating.report import analyze, render
from basemode_loom.store import GenerationStore


def _batch(store, parent_id, generation_id, models):
    """One generation batch: several models answering the same prompt."""
    return [
        store.add_child(
            parent_id,
            f" {model}-{index}",
            model=model,
            strategy="system",
            max_tokens=10,
            temperature=0.9,
            metadata={"generation_id": generation_id, "model_idx": index},
        )
        for index, model in enumerate(models)
    ]


@pytest.fixture
def disconnected_cohorts(tmp_path):
    """Two pairs of evenly matched models that never share a batch.

    The comparison graph has two components, so no amount of further looming
    inside either one can place them on a common scale.
    """
    store = GenerationStore(tmp_path / "disconnected.sqlite")
    root = store.create_root("Root")
    tip = root.id
    for step in range(5):
        first, second = _batch(store, tip, f"g-a{step}", ["a1", "a2"])
        # alternate the winner so neither model dominates the other
        tip = (first if step % 2 else second).id
    branch = root.id
    for step in range(5):
        first, second = _batch(store, branch, f"g-b{step}", ["b1", "b2"])
        branch = (first if step % 2 else second).id
    return store, root


@pytest.fixture
def two_cohorts(tmp_path):
    """Two model cohorts that only meet through a single bridge batch.

    ``strong`` beats ``middle`` and ``middle`` beats ``weak``, but ``strong``
    and ``weak`` never share a batch except once, so their rating difference is
    an inference through ``middle``.
    """
    store = GenerationStore(tmp_path / "rating.sqlite")
    root = store.create_root("Root")
    tip = root.id
    for step in range(6):
        winner, loser = _batch(store, tip, f"g-a{step}", ["strong", "middle"])
        tip = winner.id
    branch = tip
    for step in range(6):
        winner, loser = _batch(store, branch, f"g-b{step}", ["middle", "weak"])
        branch = winner.id
    return store, root


def test_batches_group_by_generation_and_keep_slot_order(two_cohorts):
    store, root = two_cohorts
    batches = batches_from_store(store, root_ids=[root.id])
    assert len(batches) == 12
    assert all(len(batch.completions) == 2 for batch in batches)
    assert all(batch.is_mixed for batch in batches)
    for batch in batches:
        assert [c.position for c in batch.completions] == [0, 1]


def test_a_batch_nobody_continued_from_is_not_a_draw(two_cohorts):
    store, root = two_cohorts
    batches = batches_from_store(store, root_ids=[root.id])
    abandoned = [b for b in batches if not b.decisive_for("descendant")]
    assert abandoned, "the last batch in each chain was never expanded"
    kept = comparisons_from_batches(batches, drop_indecisive=True)
    dropped = comparisons_from_batches(batches, drop_indecisive=False)
    assert len(kept) < len(dropped)


def test_ratings_order_models_through_a_shared_opponent(two_cohorts):
    store, root = two_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    fit = fit_davidson(comparisons)
    order = [rating.model for rating in fit.ratings]
    assert order == ["strong", "middle", "weak"]
    # strong and weak never met, but the scale still separates them
    by_model = fit.by_model()
    assert by_model["strong"].elo > by_model["weak"].elo


def test_graph_reports_the_edge_everything_rests_on(two_cohorts):
    store, root = two_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    graph = graph_summary(comparisons)
    assert graph.connected
    bridges = {(a, b) for a, b, _ in graph.bridges}
    assert ("middle", "strong") in bridges or ("strong", "middle") in bridges


def test_ties_get_their_own_parameter(two_cohorts):
    store, root = two_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id]), drop_indecisive=False
    )
    assert any(comparison.result == TIE for comparison in comparisons)
    fit = fit_davidson(comparisons)
    assert fit.tie_param > 0
    assert fit.converged


def test_bootstrap_gives_wider_intervals_to_thinner_models(two_cohorts):
    store, root = two_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    fit = bootstrap_ratings(comparisons, resamples=40)
    for rating in fit.ratings:
        assert rating.lo is not None and rating.hi is not None
        assert rating.lo <= rating.elo <= rating.hi


def test_recommendation_bridges_two_disconnected_cohorts(disconnected_cohorts):
    store, root = disconnected_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    graph = graph_summary(comparisons)
    assert not graph.connected, "two cohorts that never met"

    fit = fit_davidson(comparisons)
    chosen, before, after = recommend_cohort(comparisons, fit, size=2)
    assert {c[0] for c in chosen} == {"a", "b"}, "one model from each component"
    assert after < before


def test_repeating_a_saturated_line_up_is_worth_almost_nothing(disconnected_cohorts):
    store, root = disconnected_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    fit = fit_davidson(comparisons)
    models, base = information_matrix(comparisons, fit)
    before = total_variance(models, base)

    def gain(pair):
        _, matrix = information_matrix(
            comparisons, fit, extra=[(pair[0], pair[1], 20.0)]
        )
        return before / total_variance(models, matrix)

    assert gain(("a1", "b1")) > gain(("a1", "a2")), (
        "bridging beats deepening an edge that already has comparisons"
    )


def test_pair_uncertainty_is_widest_for_the_pair_that_never_met(two_cohorts):
    store, root = two_cohorts
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id])
    )
    pairs = pair_uncertainty(comparisons, fit_davidson(comparisons))
    assert {pairs[0].left, pairs[0].right} == {"strong", "weak"}


def test_sessions_and_deliberation_are_derived_from_timestamps(two_cohorts):
    store, root = two_cohorts
    batches = batches_from_store(store, root_ids=[root.id])
    assert all(batch.session == 0 for batch in batches), "one sitting"
    gaps = deliberation_seconds(batches)
    assert all(value >= 0 for value in gaps.values())


def test_gateway_variants_of_one_model_merge():
    assert naming.canonical("together_ai/moonshotai/kimi-k3") == "kimi-k3"
    assert naming.canonical("moonshot/kimi-k3") == "kimi-k3"
    # a dated pin is not assumed to be the same snapshot
    assert naming.canonical("deepseek-v4-flash-0731") != naming.canonical(
        "deepseek-v4-flash"
    )
    variants = naming.gateway_variants(
        ["moonshot/kimi-k3", "together_ai/moonshotai/kimi-k3", "openai/gpt-5.4"]
    )
    assert set(variants) == {"kimi-k3"}


def test_manual_nodes_are_not_rated_against_models(tmp_path):
    store = GenerationStore(tmp_path / "manual.sqlite")
    root = store.create_root("Root")
    _batch(store, root.id, "g0", ["manual", "strong"])
    comparisons = comparisons_from_batches(
        batches_from_store(store, root_ids=[root.id]),
        exclude=naming.NON_MODELS,
        drop_indecisive=False,
    )
    assert comparisons == []


def test_report_renders_and_serializes(two_cohorts):
    store, root = two_cohorts
    report = analyze(
        batches_from_store(store, root_ids=[root.id]), min_games=0, resamples=20
    )
    text = render(report)
    assert "COMPARISON GRAPH" in text
    assert "GENERATE THIS NEXT" in text
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["connected"] is True
    assert {r["model"] for r in payload["ratings"]} == {"strong", "middle", "weak"}
