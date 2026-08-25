"""Cohort-aware model rating from loom trees.

The store already records the only thing that matters for rating a model as a
writing tool: which completion the user carried on from. Every generation batch
is a controlled multi-way comparison -- same prompt, same context, same moment --
so a loom tree is a tournament that was played by accident.

:mod:`basemode_loom.stats` scores nodes *within* a batch, which is the right
unit but leaves the scores on a per-batch scale: the batch mean is 1.0 by
construction, so ``normalized_peer_descendant_score`` says how a model did
against whoever was standing next to it, not how good it is. Averaging that per
model compares scores computed against different reference populations.

This package fits a tie-aware Bradley-Terry model over the pairwise comparisons
the batches already contain, which puts every model on one latent scale even
when two of them never met, by routing through shared opponents. It also
reports how far the ratings can be trusted: comparison-graph connectivity, per
pair standard errors, and which line-up to generate next.

Pure stdlib.
"""

from .batches import (
    SESSION_GAP_SECONDS,
    SIGNALS,
    Batch,
    Completion,
    batches_from_store,
    batches_from_tree,
    deliberation_seconds,
    iter_completions,
    segment_sessions,
    sessions,
)
from .comparisons import (
    LOSS,
    TIE,
    WIN,
    Comparison,
    GraphSummary,
    cohorts,
    comparisons_from_batches,
    graph_summary,
    opponents,
)
from .davidson import (
    ELO_PER_LOGIT,
    Fit,
    Rating,
    bootstrap_ratings,
    depth_buckets,
    fit_davidson,
    rating_table,
)
from .design import (
    PairUncertainty,
    information_matrix,
    pair_uncertainty,
    recommend_cohort,
    total_variance,
)

__all__ = [
    "ELO_PER_LOGIT",
    "LOSS",
    "SESSION_GAP_SECONDS",
    "SIGNALS",
    "TIE",
    "WIN",
    "Batch",
    "Comparison",
    "Completion",
    "Fit",
    "GraphSummary",
    "PairUncertainty",
    "Rating",
    "batches_from_store",
    "batches_from_tree",
    "bootstrap_ratings",
    "cohorts",
    "comparisons_from_batches",
    "deliberation_seconds",
    "depth_buckets",
    "fit_davidson",
    "graph_summary",
    "information_matrix",
    "iter_completions",
    "opponents",
    "pair_uncertainty",
    "rating_table",
    "recommend_cohort",
    "segment_sessions",
    "sessions",
    "total_variance",
]
