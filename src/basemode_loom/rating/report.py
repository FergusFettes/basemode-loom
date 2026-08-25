"""Render a rating analysis as text or as a JSON-serializable dict.

The ranking is the least important part of the output. A ranking presented
without its comparison graph is a confidence trick, because the ordering
between two models that never met is an inference through shared opponents and
can rest on a handful of comparisons. So the report leads with the graph, and
ends with what is still unmeasured and what to generate next.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import naming
from .batches import Batch, deliberation_seconds, iter_completions, sessions
from .comparisons import Comparison, cohorts, comparisons_from_batches, graph_summary
from .davidson import Fit, bootstrap_ratings, depth_buckets, fit_davidson, rating_table
from .design import pair_uncertainty, recommend_cohort


@dataclass
class RatingReport:
    batches: list[Batch]
    comparisons: list[Comparison]
    fit: Fit
    held_out: dict[str, int]
    merged_variants: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        graph = graph_summary(self.comparisons)
        return {
            "batches": len(self.batches),
            "mixed_batches": sum(1 for b in self.batches if b.is_mixed),
            "sessions": len(sessions(self.batches)),
            "comparisons": graph.total,
            "tie_rate": graph.tie_rate,
            "connected": graph.connected,
            "components": graph.components,
            "bridges": [
                {"left": a, "right": b, "games": n} for a, b, n in graph.bridges
            ],
            "tie_parameter": self.fit.tie_param,
            "position_bias_elo_per_slot": self.fit.position_bias * 173.71779276130073,
            "held_out": self.held_out,
            "merged_variants": self.merged_variants,
            "ratings": [
                {
                    "model": r.model,
                    "elo": r.elo,
                    "low": r.lo,
                    "high": r.hi,
                    "comparisons": r.games,
                    "wins": r.wins,
                    "ties": r.ties,
                    "losses": r.losses,
                    "opponents": r.opponents,
                }
                for r in self.fit.ratings
            ],
        }


def analyze(
    batches: Sequence[Batch],
    *,
    signal: str = "descendant",
    merge_gateways: bool = True,
    keep_indecisive: bool = False,
    models: Sequence[str] | None = None,
    min_games: int = 20,
    prior_sd: float = 2.0,
    resamples: int = 200,
) -> RatingReport:
    """Fit ratings over a set of batches, with the usual hygiene applied."""
    listed = list(batches)
    name = naming.canonical if merge_gateways else (lambda model: model)
    variants = (
        naming.gateway_variants({c.model for c in iter_completions(listed)})
        if merge_gateways
        else {}
    )
    comparisons = comparisons_from_batches(
        listed,
        signal=signal,
        drop_indecisive=not keep_indecisive,
        name=name,
        exclude=naming.NON_MODELS,
    )
    if models:
        wanted = {m.strip() for m in models if m.strip()}
        comparisons = [c for c in comparisons if c.left in wanted and c.right in wanted]

    held_out: dict[str, int] = {}
    if min_games:
        counts: Counter[str] = Counter()
        for comparison in comparisons:
            counts[comparison.left] += 1
            counts[comparison.right] += 1
        keep = {model for model, n in counts.items() if n >= min_games}
        held_out = {
            model: n for model, n in sorted(counts.items()) if model not in keep
        }
        comparisons = [c for c in comparisons if c.left in keep and c.right in keep]

    fit = bootstrap_ratings(
        comparisons,
        resamples=resamples,
        prior_sd=prior_sd,
        fit_position_bias=True,
    )
    return RatingReport(listed, comparisons, fit, held_out, variants)


def render(
    report: RatingReport,
    *,
    signal: str = "descendant",
    depth_bands: int = 3,
    cohort_size: int = 4,
    prior_sd: float = 2.0,
) -> str:
    short = naming.leaf
    lines: list[str] = []

    def rule(title: str) -> None:
        lines.append(f"\n{'=' * 78}\n{title}\n{'=' * 78}")

    batches, comparisons = report.batches, report.comparisons
    rule("CORPUS")
    per_tree: dict[str, list[Batch]] = defaultdict(list)
    for batch in batches:
        per_tree[batch.root_name].append(batch)
    lines.append(
        f"{'tree':40s} {'batches':>8s} {'mixed':>6s} {'unjudged':>9s} {'depth':>6s}"
    )
    for name, group in sorted(per_tree.items(), key=lambda kv: -len(kv[1])):
        unjudged = sum(1 for b in group if not b.decisive_for(signal))
        lines.append(
            f"{name[:40]:40s} {len(group):8d} "
            f"{sum(1 for b in group if b.is_mixed):6d} {unjudged:9d} "
            f"{max(b.depth for b in group):6d}"
        )
    lines.append(
        f"\n{len(per_tree)} trees, {len(batches)} batches, "
        f"{len(sessions(batches))} sessions, "
        f"{sum(1 for b in batches if b.is_mixed)} mixed"
    )
    gaps = sorted(deliberation_seconds(batches).values())
    if gaps:
        lines.append(
            f"deliberation before generating again: median {gaps[len(gaps) // 2]:.0f}s "
            f"(quartiles {gaps[len(gaps) // 4]:.0f}s / {gaps[3 * len(gaps) // 4]:.0f}s, "
            f"n={len(gaps)})"
        )
    if report.merged_variants:
        lines.append("\nmerged gateway variants of the same model:")
        for name, ids in sorted(report.merged_variants.items()):
            lines.append(f"  {name}: {', '.join(ids)}")

    rule("COHORTS (which line-ups were generated together)")
    for line_up, count in cohorts(batches, name=naming.canonical).most_common(20):
        lines.append(f"{count:5d}  {', '.join(short(m) for m in line_up)}")

    graph = graph_summary(comparisons)
    rule(f"COMPARISON GRAPH  (signal={signal})")
    lines.append(
        f"{graph.total} cross-model comparisons, {graph.tie_rate:.0%} ties, "
        f"{len(graph.models)} models"
    )
    lines.append(f"connected: {graph.connected}")
    if not graph.connected:
        for component in graph.components:
            lines.append(f"  component: {', '.join(short(m) for m in component)}")
    if report.held_out:
        lines.append(
            "held out, too thin to rate: "
            + ", ".join(f"{short(m)}({n})" for m, n in report.held_out.items())
        )
    lines.append("\nedges (comparisons):")
    for (a, b), count in sorted(graph.games.items(), key=lambda kv: -kv[1])[:30]:
        lines.append(f"{count:6d}  {short(a)} vs {short(b)}")
    if graph.bridges:
        lines.append("\ncut edges -- the whole cross-cohort scale rests on these:")
        for a, b, count in graph.bridges:
            lines.append(f"{count:6d}  {short(a)} vs {short(b)}")
    else:
        lines.append("\nno cut edges: every rating difference has more than one path.")

    rule("RATINGS  (Davidson Bradley-Terry, cluster bootstrap over batches)")
    lines.append(rating_table(report.fit))

    rule("DEPTH-CONDITIONAL RATINGS")
    lines.append(
        "Comparable across bands only within one stable cohort; and depth is\n"
        "confounded with time-in-session unless the tree was returned to."
    )
    for label, chunk in depth_buckets(comparisons, buckets=depth_bands):
        lines.append(f"\n--- {label}  ({len(chunk)} comparisons) ---")
        lines.append(rating_table(fit_davidson(chunk, prior_sd=prior_sd)))

    rule("WHAT IS STILL UNMEASURED  (standard error of each rating difference)")
    lines.append(f"{'pair':52s} {'diff':>7s} {'SE':>7s} {'n':>5s}  resolved")
    for pair in pair_uncertainty(comparisons, report.fit, prior_sd=prior_sd)[:15]:
        label = f"{short(pair.left)} vs {short(pair.right)}"
        lines.append(
            f"{label[:52]:52s} {pair.diff_elo:+7.0f} {pair.sd_elo:7.0f} "
            f"{pair.games:5d}  {'yes' if pair.resolved else 'NO'}"
        )

    rule(f"GENERATE THIS NEXT  (line-up of {cohort_size})")
    chosen, before, after = recommend_cohort(
        comparisons, report.fit, size=cohort_size, prior_sd=prior_sd
    )
    lines.extend(f"  - {model}" for model in chosen)
    if before:
        lines.append(
            f"\n20 batches of this line-up cut total pairwise variance "
            f"{before:.2f} -> {after:.2f} ({before / after:.2f}x). "
            "Repeating a line-up you have already run is worth close to nothing."
        )
    return "\n".join(lines)
