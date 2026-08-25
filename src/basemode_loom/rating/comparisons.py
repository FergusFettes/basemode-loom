"""Turn generation batches into pairwise comparisons.

Every mixed batch of size k contributes k*(k-1)/2 head-to-head comparisons.
The comparisons form a graph over models, and any rating claiming a common
scale is only as trustworthy as that graph is connected -- so the graph
diagnostics here matter as much as the comparisons themselves.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .batches import Batch

WIN = 1
LOSS = -1
TIE = 0


@dataclass(frozen=True)
class Comparison:
    """One head-to-head, carrying the context it happened in."""

    left: str
    right: str
    result: int  # WIN / LOSS / TIE, from ``left``'s point of view
    batch_id: str
    root_id: str
    depth: int
    batch_size: int
    left_position: int
    right_position: int
    margin: float
    session: int = 0


def comparisons_from_batches(
    batches: Iterable[Batch],
    *,
    signal: str = "descendant",
    drop_indecisive: bool = True,
    min_margin: float = 0.0,
    name: Callable[[str], str] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> list[Comparison]:
    """Explode batches into cross-model pairs.

    ``drop_indecisive`` removes batches the user never judged at all. Those are
    not evidence of a k-way draw, they are evidence the user walked away, and
    keeping them dilutes every rating toward the mean.

    ``name`` maps a recorded model id to the identity to rate it under; pass
    :func:`basemode_loom.rating.naming.canonical` to merge gateway variants.
    """
    label = name or (lambda model: model)
    out: list[Comparison] = []
    for batch in batches:
        if drop_indecisive and not batch.decisive_for(signal):
            continue
        items = batch.completions
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                left, right = label(a.model), label(b.model)
                if left == right or left in exclude or right in exclude:
                    continue
                margin = a.outcome(signal) - b.outcome(signal)
                if abs(margin) <= min_margin:
                    result = TIE
                elif margin > 0:
                    result = WIN
                else:
                    result = LOSS
                out.append(
                    Comparison(
                        left=left,
                        right=right,
                        result=result,
                        batch_id=batch.batch_id,
                        root_id=batch.root_id,
                        depth=batch.depth,
                        batch_size=len(items),
                        left_position=a.position,
                        right_position=b.position,
                        margin=margin,
                        session=batch.session,
                    )
                )
    return out


@dataclass(frozen=True)
class GraphSummary:
    """Connectivity of the comparison graph, and what holds it together."""

    models: list[str]
    games: dict[tuple[str, str], int]
    components: list[list[str]]
    #: edges whose removal disconnects the graph -- the cross-cohort scale
    #: rests entirely on these, so a bridge carrying five games is a warning
    bridges: list[tuple[str, str, int]]
    tie_rate: float
    total: int

    @property
    def connected(self) -> bool:
        return len(self.components) <= 1


def graph_summary(comparisons: Sequence[Comparison]) -> GraphSummary:
    games: Counter[tuple[str, str]] = Counter()
    ties = 0
    for comparison in comparisons:
        games[_edge(comparison.left, comparison.right)] += 1
        if comparison.result == TIE:
            ties += 1
    models = sorted({model for edge in games for model in edge})
    components = _components(models, set(games))

    bridges = []
    for edge, count in games.items():
        if len(_components(models, set(games) - {edge})) > len(components):
            bridges.append((edge[0], edge[1], count))
    bridges.sort(key=lambda bridge: bridge[2])

    total = sum(games.values())
    return GraphSummary(
        models=models,
        games=dict(games),
        components=components,
        bridges=bridges,
        tie_rate=ties / total if total else 0.0,
        total=total,
    )


def _edge(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _components(models: Sequence[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen: set[str] = set()
    out = []
    for model in models:
        if model in seen:
            continue
        stack, group = [model], []
        seen.add(model)
        while stack:
            current = stack.pop()
            group.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        out.append(sorted(group))
    return sorted(out, key=len, reverse=True)


def opponents(comparisons: Sequence[Comparison]) -> dict[str, Counter]:
    """Per-model opponent counts -- who each model actually got measured against."""
    out: dict[str, Counter] = defaultdict(Counter)
    for comparison in comparisons:
        out[comparison.left][comparison.right] += 1
        out[comparison.right][comparison.left] += 1
    return dict(out)


def cohorts(
    batches: Iterable[Batch], *, name: Callable[[str], str] | None = None
) -> Counter:
    """How often each exact model line-up was generated together."""
    label = name or (lambda model: model)
    return Counter(
        tuple(sorted({label(c.model) for c in batch.completions})) for batch in batches
    )
