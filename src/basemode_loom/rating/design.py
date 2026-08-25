"""Which models should share the next batch?

Rating quality here is not limited by node count -- it is limited by the
connectivity of the comparison graph. A tree with a thousand nodes that only
ever batched A-vs-B and C-vs-D tells you nothing about A vs C, no matter how
many nodes you add.

For Bradley-Terry, the variance of an estimated rating *difference* is the
effective resistance between the two models in a graph whose edge weights are
the Fisher information of the comparisons observed on that edge. That gives a
clean answer to "what should I loom next": the line-up that removes the most
total resistance.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from .comparisons import Comparison
from .davidson import ELO_PER_LOGIT, Fit


@dataclass(frozen=True)
class PairUncertainty:
    left: str
    right: str
    diff_elo: float
    sd_elo: float
    games: int

    @property
    def resolved(self) -> bool:
        """Is the sign of this comparison actually established?"""
        return abs(self.diff_elo) > 2 * self.sd_elo


def _edge_weight(theta_i: float, theta_j: float, nu: float) -> float:
    """Fisher information a single comparison on edge (i, j) carries.

    For Davidson this is the variance of the score contribution; the
    Bradley-Terry limit p_i p_j / (p_i + p_j)^2 is the nu -> 0 case and is
    close enough for design purposes. Ties are uninformative about ordering,
    so a high tie rate deflates every edge.
    """
    d = theta_i - theta_j
    bt = 1.0 / (2.0 + math.exp(d) + math.exp(-d))  # p_i p_j / (p_i+p_j)^2
    tie_share = nu / (nu + 2.0 * math.cosh(d / 2.0))
    return bt * (1.0 - tie_share)


def information_matrix(
    comparisons: Sequence[Comparison],
    fit: Fit,
    *,
    prior_sd: float = 2.0,
    extra: Sequence[tuple[str, str, float]] = (),
) -> tuple[list[str], list[list[float]]]:
    """Weighted graph Laplacian + prior, in the model ordering returned."""
    theta = {r.model: r.logit for r in fit.ratings}
    models = sorted(theta)
    index = {m: i for i, m in enumerate(models)}
    n = len(models)
    matrix = [[0.0] * n for _ in range(n)]

    counts: Counter[tuple[str, str]] = Counter()
    for c in comparisons:
        key = (c.left, c.right) if c.left <= c.right else (c.right, c.left)
        counts[key] += 1
    for (a, b), count in counts.items():
        if a not in index or b not in index:
            continue
        _add_edge(
            matrix,
            index[a],
            index[b],
            count * _edge_weight(theta[a], theta[b], fit.tie_param),
        )
    for a, b, count in extra:
        if a in index and b in index:
            _add_edge(
                matrix,
                index[a],
                index[b],
                count * _edge_weight(theta[a], theta[b], fit.tie_param),
            )

    precision = 1.0 / (prior_sd * prior_sd) if prior_sd else 1e-6
    for i in range(n):
        matrix[i][i] += precision
    return models, matrix


def _add_edge(matrix: list[list[float]], i: int, j: int, weight: float) -> None:
    matrix[i][i] += weight
    matrix[j][j] += weight
    matrix[i][j] -= weight
    matrix[j][i] -= weight


def invert(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    work = [
        row[:] + [1.0 if i == j else 0.0 for j in range(n)]
        for i, row in enumerate(matrix)
    ]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < 1e-15:
            work[col][col] += 1e-9
            pivot = col
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [value / scale for value in work[col]]
        for row in range(n):
            if row == col:
                continue
            factor = work[row][col]
            if factor:
                work[row] = [
                    a - factor * b for a, b in zip(work[row], work[col], strict=True)
                ]
    return [row[n:] for row in work]


def pair_uncertainty(
    comparisons: Sequence[Comparison], fit: Fit, *, prior_sd: float = 2.0
) -> list[PairUncertainty]:
    """Standard error of every pairwise rating difference, widest first."""
    models, matrix = information_matrix(comparisons, fit, prior_sd=prior_sd)
    inverse = invert(matrix)
    index = {m: i for i, m in enumerate(models)}
    theta = {r.model: r.logit for r in fit.ratings}
    games: Counter[tuple[str, str]] = Counter()
    for c in comparisons:
        key = (c.left, c.right) if c.left <= c.right else (c.right, c.left)
        games[key] += 1

    out = []
    for a, b in combinations(models, 2):
        i, j = index[a], index[b]
        variance = inverse[i][i] + inverse[j][j] - 2 * inverse[i][j]
        out.append(
            PairUncertainty(
                left=a,
                right=b,
                diff_elo=(theta[a] - theta[b]) * ELO_PER_LOGIT,
                sd_elo=math.sqrt(max(variance, 0.0)) * ELO_PER_LOGIT,
                games=games.get((a, b) if a <= b else (b, a), 0),
            )
        )
    out.sort(key=lambda p: -p.sd_elo)
    return out


def total_variance(models: list[str], matrix: list[list[float]]) -> float:
    """A-optimality: summed variance of all pairwise differences.

    For a Laplacian-plus-prior this equals n*trace(inv) - sum(inv), which is
    what we minimize when choosing a line-up.
    """
    inverse = invert(matrix)
    n = len(models)
    trace = sum(inverse[i][i] for i in range(n))
    total = sum(sum(row) for row in inverse)
    return n * trace - total


def recommend_cohort(
    comparisons: Sequence[Comparison],
    fit: Fit,
    *,
    size: int = 4,
    batches: int = 20,
    prior_sd: float = 2.0,
    candidates: Sequence[str] | None = None,
) -> tuple[list[str], float, float]:
    """Greedily pick the line-up whose repeated use shrinks the ratings most.

    Returns (models, variance_before, variance_after) where the variances are
    the A-optimality criterion in logit^2. Greedy is not optimal but the
    candidate set is small and the objective is close to submodular in
    practice; the winner is usually obvious anyway -- it is whichever line-up
    straddles the weakest bridge.
    """
    pool = list(candidates) if candidates else [r.model for r in fit.ratings]
    models, base = information_matrix(comparisons, fit, prior_sd=prior_sd)
    before = total_variance(models, base)

    chosen: list[str] = []
    for _ in range(min(size, len(pool))):
        best, best_score = None, None
        for candidate in pool:
            if candidate in chosen:
                continue
            trial = [*chosen, candidate]
            extra = [(a, b, float(batches)) for a, b in combinations(sorted(trial), 2)]
            _, matrix = information_matrix(
                comparisons, fit, prior_sd=prior_sd, extra=extra
            )
            score = total_variance(models, matrix)
            if best_score is None or score < best_score:
                best, best_score = candidate, score
        if best is None:
            break
        chosen.append(best)
    extra = [(a, b, float(batches)) for a, b in combinations(sorted(chosen), 2)]
    _, matrix = information_matrix(comparisons, fit, prior_sd=prior_sd, extra=extra)
    return sorted(chosen), before, total_variance(models, matrix)
