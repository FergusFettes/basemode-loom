"""Generation batches: the unit every rating in this package is built on.

A batch is the set of siblings sharing a parent and a ``generation_id`` -- the
completions the user saw side by side and chose between. One prompt, one
context, one moment, one attention span. Everything that makes cross-context
comparison hard is held constant inside it, which is why the batch and not the
node is the atom here.

This module reduces an :class:`~basemode_loom.loom_formats.AnalysisTree` to
batches, reusing :mod:`basemode_loom.stats` for the descendant scores so the
two modules cannot drift apart, and adds the timing structure the store already
records but nothing yet reads: sittings, position within a sitting, and how long
the writer deliberated before generating again.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ..loom_formats import AnalysisNode, AnalysisTree, tree_from_store
from ..stats import NodeScores, analyze_analysis_tree
from ..store import GenerationStore

#: A gap longer than this starts a new sitting. The inter-batch gap
#: distribution is strongly bimodal -- seconds within a sitting, hours between
#: them -- so nothing hinges on the exact cut.
SESSION_GAP_SECONDS = 1800.0

SIGNALS = ("descendant", "discounted", "click", "bookmark")


@dataclass(frozen=True)
class Completion:
    """One generated node, seen from inside the batch it was born in."""

    node_id: str
    root_id: str
    model: str
    # revealed preference, from basemode_loom.stats
    descendant_score: float
    discounted_descendant_score: float
    expanded: bool
    bookmarked: bool
    hidden: bool
    rating: float | None
    # covariates worth controlling for
    depth: int
    position: int
    chars: int
    created_at: str | None = None
    elapsed_ms: float | None = None
    ttft_ms: float | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    edited: bool = False

    @property
    def when(self) -> datetime | None:
        return parse_time(self.created_at)

    def outcome(self, signal: str) -> float:
        """Scalar used to order completions inside a batch."""
        if signal == "descendant":
            return self.descendant_score
        if signal == "discounted":
            return self.discounted_descendant_score
        if signal == "click":
            return 1.0 if self.expanded else 0.0
        if signal == "bookmark":
            return 1.0 if self.bookmarked else 0.0
        raise ValueError(f"unknown signal: {signal!r}")


@dataclass(frozen=True)
class Batch:
    """One prompt, several models, one decision."""

    batch_id: str
    root_id: str
    root_name: str
    parent_id: str
    depth: int
    completions: tuple[Completion, ...]
    #: 0-based sitting index within the tree, from :func:`segment_sessions`
    session: int = 0
    #: minutes from the start of that sitting
    minutes_into_session: float = 0.0

    @property
    def models(self) -> frozenset[str]:
        return frozenset(c.model for c in self.completions)

    @property
    def is_mixed(self) -> bool:
        """Does this batch compare different models at all?"""
        return len(self.models) > 1

    @property
    def when(self) -> datetime | None:
        """When the batch was generated -- its earliest completion."""
        moments = [c.when for c in self.completions if c.when]
        return min(moments) if moments else None

    def decisive_for(self, signal: str) -> bool:
        """Did the user express a preference here, or abandon the whole set?

        An all-zero batch is not a batch every model lost, it is a batch that
        was never judged. :mod:`basemode_loom.stats` already skips these for
        the peer score; they should be skipped for ratings too.
        """
        return max((c.outcome(signal) for c in self.completions), default=0.0) > 0.0


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def batches_from_tree(
    tree: AnalysisTree, *, name: str = "", gap_seconds: float = SESSION_GAP_SECONDS
) -> list[Batch]:
    """Reduce one analysis tree to its generation batches."""
    stats = analyze_analysis_tree(tree)
    by_id = {node.id: node for node in tree.nodes}
    grouped: dict[tuple[str, str], list[NodeScores]] = defaultdict(list)
    for score in stats.node_scores:
        if score.parent_id is None or not score.model:
            continue
        grouped[(score.parent_id, score.generation_id or "children")].append(score)

    label = name or tree.root_id[:8]
    batches = []
    for (parent_id, generation_id), scores in grouped.items():
        if len(scores) < 2:
            continue
        scores.sort(key=lambda score: _slot(by_id.get(score.node_id)))
        batches.append(
            Batch(
                batch_id=f"{tree.root_id}:{parent_id}:{generation_id}",
                root_id=tree.root_id,
                root_name=label,
                parent_id=parent_id,
                depth=scores[0].depth,
                completions=tuple(
                    _completion(score, by_id.get(score.node_id), tree.root_id, position)
                    for position, score in enumerate(scores)
                ),
            )
        )
    return segment_sessions(batches, gap_seconds=gap_seconds)


def batches_from_store(
    store: GenerationStore,
    *,
    root_ids: Iterable[str] | None = None,
    include_archived: bool = False,
) -> list[Batch]:
    """Every batch in the store, or in the named roots."""
    if root_ids is None:
        roots = store.roots(archived=None if include_archived else False)
        wanted = [(root.id, _root_name(store, root.id)) for root in roots]
    else:
        wanted = [(root_id, _root_name(store, root_id)) for root_id in root_ids]
    batches: list[Batch] = []
    for root_id, name in wanted:
        batches.extend(batches_from_tree(tree_from_store(store, root_id), name=name))
    return batches


def _root_name(store: GenerationStore, root_id: str) -> str:
    tree = store.get_tree(root_id)
    name = getattr(tree, "name", None) if tree else None
    return str(name) if name else root_id[:8]


def _slot(node: AnalysisNode | None) -> tuple[int, str]:
    """Where this completion sat in the list the user read.

    The generator records it as ``model_idx``. Ordering by arrival time instead
    is a trap: completions stream back out of order, so the fast models finish
    first however they were listed, and the proxy ends up encoding model speed.
    """
    if node is None:
        return (10**6, "")
    if node.branch_index is not None:
        return (node.branch_index, node.created_at or "")
    for key in ("model_idx", "model_branch_index"):
        value = node.metadata.get(key)
        if isinstance(value, int):
            return (value, node.created_at or "")
    return (10**6, node.created_at or "")


def _completion(
    score: NodeScores, node: AnalysisNode | None, root_id: str, position: int
) -> Completion:
    metadata: dict[str, Any] = node.metadata if node else {}
    timing = metadata.get("timing") if isinstance(metadata.get("timing"), dict) else {}
    usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    return Completion(
        node_id=score.node_id,
        root_id=root_id,
        model=score.model or "",
        descendant_score=score.descendant_score,
        discounted_descendant_score=score.discounted_descendant_score,
        expanded=score.expanded,
        bookmarked=score.bookmarked,
        hidden=score.hidden,
        rating=score.rating,
        depth=score.depth,
        position=position,
        chars=len(node.text) if node else 0,
        created_at=node.created_at if node else None,
        elapsed_ms=timing.get("elapsed_ms"),
        ttft_ms=timing.get("ttft_ms"),
        completion_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("cost_usd"),
        edited=bool(metadata.get("in_place_edits")),
    )


def segment_sessions(
    batches: list[Batch], *, gap_seconds: float = SESSION_GAP_SECONDS
) -> list[Batch]:
    """Split each tree's batches into sittings and stamp position within one.

    Depth and time-in-session are easy to confuse. A tree written straight
    through has them perfectly correlated, and any depth effect found in it is
    indistinguishable from the writer's standards drifting. A tree the writer
    came back to -- branching from a shallow node weeks later -- separates them.
    """
    timed = sorted(
        (batch for batch in batches if batch.when),
        key=lambda batch: (batch.root_id, batch.when),
    )
    out = [batch for batch in batches if not batch.when]
    index = 0
    previous_root: str | None = None
    previous_time: datetime | None = None
    start: datetime | None = None
    for batch in timed:
        moment = batch.when
        assert moment is not None
        if batch.root_id != previous_root:
            index, start = 0, moment
        elif previous_time and (moment - previous_time).total_seconds() > gap_seconds:
            index, start = index + 1, moment
        minutes = (moment - start).total_seconds() / 60.0 if start else 0.0
        out.append(replace(batch, session=index, minutes_into_session=minutes))
        previous_root, previous_time = batch.root_id, moment
    out.sort(key=lambda batch: (batch.depth, batch.batch_id))
    return out


def deliberation_seconds(
    batches: Iterable[Batch], *, cap_seconds: float = SESSION_GAP_SECONDS
) -> dict[str, float]:
    """How long the writer spent on a batch before generating the next one.

    The gap from a batch arriving to the next batch being requested beneath one
    of its completions. It is the closest thing the store has to an attention
    measurement, and a decision made in four seconds is weaker evidence than one
    made in four minutes.
    """
    listed = list(batches)
    parent_of = {c.node_id: batch for batch in listed for c in batch.completions}
    out: dict[str, float] = {}
    for batch in listed:
        parent = parent_of.get(batch.parent_id)
        if parent is None or not parent.when or not batch.when:
            continue
        gap = (batch.when - parent.when).total_seconds()
        if 0 <= gap <= cap_seconds:
            out[parent.batch_id] = min(out.get(parent.batch_id, gap), gap)
    return out


def sessions(batches: Iterable[Batch]) -> set[tuple[str, int]]:
    return {(batch.root_id, batch.session) for batch in batches}


def iter_completions(batches: Iterable[Batch]) -> Iterator[Completion]:
    for batch in batches:
        yield from batch.completions
