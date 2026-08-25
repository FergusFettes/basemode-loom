"""Tie-aware Bradley-Terry ratings, fitted by gradient ascent. Pure stdlib.

Why not just average the within-batch scores? Because a within-batch score is
normalized against whoever happened to be in that batch. Averaging those
across batches compares z-scores computed against different reference
populations -- a model looks strong for having drawn weak line-ups. A
Bradley-Terry fit instead puts every model on one latent scale and propagates
strength through shared opponents, which is the only thing that makes cohorts
commensurable.

The model is Davidson (1970), which gives ties their own parameter instead of
scoring them as half a win:

    P(i beats j) = a / (a + b + nu*sqrt(a*b))
    P(tie)       = nu*sqrt(a*b) / (a + b + nu*sqrt(a*b))

with a = exp(theta_i), b = exp(theta_j). Loom data is majority ties (most
siblings are never touched), so this matters more here than in a chess league.

A weak Gaussian prior on theta keeps thinly-connected models finite instead of
running off to +/- infinity, and an optional position term absorbs slot bias.
That term is not decorative: on one real corpus it fits at -43 elo per slot
(95% CI [-66, -24]), a larger effect than the gap between several adjacent
models, and it favours *later* slots rather than earlier ones. Randomising the
order completions are displayed in removes the effect at the source, which is
better than modelling it.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .comparisons import TIE, WIN, Comparison

ELO_PER_LOGIT = 400.0 / math.log(10.0)  # ~173.7
ELO_ANCHOR = 1500.0


@dataclass(frozen=True)
class Rating:
    model: str
    logit: float
    elo: float
    games: int
    wins: float
    losses: float
    ties: int
    opponents: int
    lo: float | None = None  # bootstrap CI, in elo
    hi: float | None = None

    @property
    def ci_width(self) -> float | None:
        if self.lo is None or self.hi is None:
            return None
        return self.hi - self.lo


@dataclass(frozen=True)
class Fit:
    ratings: list[Rating]
    tie_param: float
    position_bias: float
    log_likelihood: float
    iterations: int
    converged: bool
    position_bias_ci: tuple[float, float] | None = None

    def by_model(self) -> dict[str, Rating]:
        return {r.model: r for r in self.ratings}


def fit_davidson(
    comparisons: Sequence[Comparison],
    *,
    prior_sd: float = 2.0,
    fit_position_bias: bool = False,
    max_iter: int = 20000,
    tol: float = 1e-9,
) -> Fit:
    """Maximum a posteriori Davidson fit over a list of comparisons."""
    models = sorted({m for c in comparisons for m in (c.left, c.right)})
    if not models:
        return Fit([], 1.0, 0.0, 0.0, 0, True)
    index = {m: i for i, m in enumerate(models)}

    # The likelihood only depends on sufficient statistics: per (pair,
    # position) group, how many wins each side took and how many ties. For a
    # dozen models over a thousand comparisons that is a ~40x smaller inner
    # loop, which is what makes the bootstrap affordable in pure python.
    groups: dict[tuple[int, int, float, float], list[int]] = {}
    for c in comparisons:
        i, j = index[c.left], index[c.right]
        # Centre the slot index inside its batch, so the position term is
        # orthogonal to the overall scale rather than fighting the gauge.
        middle = (c.batch_size - 1) / 2.0
        pi, pj = c.left_position - middle, c.right_position - middle
        if not fit_position_bias:
            pi = pj = 0.0
        if i > j:
            i, j, pi, pj = j, i, pj, pi
            result = -c.result
        else:
            result = c.result
        entry = groups.setdefault((i, j, pi, pj), [0, 0, 0])
        entry[0 if result == WIN else (1 if result == -WIN else 2)] += 1
    data = [
        (i, j, pi, pj, wins_i, wins_j, ties)
        for (i, j, pi, pj), (wins_i, wins_j, ties) in groups.items()
    ]

    theta = [0.0] * len(models)
    lam = 0.0  # log nu; nu = 1 is "ties as likely as the BT geometric mean"
    gamma = 0.0
    precision = 1.0 / (prior_sd * prior_sd) if prior_sd else 0.0

    def objective(theta, lam, gamma) -> float:
        total = 0.0
        for i, j, pi, pj, wins_i, wins_j, ties in data:
            u = theta[i] - gamma * pi
            v = theta[j] - gamma * pj
            m = max(u, v)
            a = math.exp(u - m)
            b = math.exp(v - m)
            g = math.exp(lam) * math.sqrt(a * b)
            log_d = m + math.log(a + b + g)
            total += wins_i * u + wins_j * v + ties * (lam + (u + v) / 2.0)
            total -= (wins_i + wins_j + ties) * log_d
        if precision:
            total -= 0.5 * precision * sum(t * t for t in theta)
        return total

    def gradient(theta, lam, gamma):
        g_theta = [0.0] * len(models)
        g_lam = 0.0
        g_gamma = 0.0
        for i, j, pi, pj, wins_i, wins_j, ties in data:
            u = theta[i] - gamma * pi
            v = theta[j] - gamma * pj
            m = max(u, v)
            a = math.exp(u - m)
            b = math.exp(v - m)
            g = math.exp(lam) * math.sqrt(a * b)
            d = a + b + g
            n = wins_i + wins_j + ties
            share_i = (a + g / 2.0) / d
            share_j = (b + g / 2.0) / d
            g_theta[i] += wins_i + 0.5 * ties - n * share_i
            g_theta[j] += wins_j + 0.5 * ties - n * share_j
            g_lam += ties - n * (g / d)
            if fit_position_bias:
                observed = -(wins_i * pi + wins_j * pj + ties * (pi + pj) / 2.0)
                expected = -(a * pi + b * pj + g * (pi + pj) / 2.0) / d
                g_gamma += observed - n * expected
        if precision:
            for k in range(len(models)):
                g_theta[k] -= precision * theta[k]
        return g_theta, g_lam, g_gamma

    current = objective(theta, lam, gamma)
    step = 0.1
    converged = False
    iterations = 0
    for iterations in range(1, max_iter + 1):  # noqa: B007
        g_theta, g_lam, g_gamma = gradient(theta, lam, gamma)
        norm = math.sqrt(sum(x * x for x in g_theta) + g_lam**2 + g_gamma**2)
        if norm < 1e-8:
            converged = True
            break
        # backtracking line search on the exact objective
        candidate = None
        while step > 1e-14:
            trial_theta = [t + step * g for t, g in zip(theta, g_theta, strict=True)]
            mean = sum(trial_theta) / len(trial_theta)
            trial_theta = [t - mean for t in trial_theta]  # fix the gauge
            trial_lam = lam + step * g_lam
            trial_gamma = gamma + step * g_gamma
            candidate = objective(trial_theta, trial_lam, trial_gamma)
            if candidate >= current:
                break
            step /= 2.0
        if candidate is None or step <= 1e-14:
            converged = True
            break
        improvement = candidate - current
        theta, lam, gamma, current = trial_theta, trial_lam, trial_gamma, candidate
        if improvement < tol:
            converged = True
            break
        step *= 1.3

    tally = _tally(comparisons)
    ratings = [
        Rating(
            model=model,
            logit=theta[index[model]],
            elo=ELO_ANCHOR + ELO_PER_LOGIT * theta[index[model]],
            games=tally[model]["games"],
            wins=tally[model]["wins"],
            losses=tally[model]["losses"],
            ties=tally[model]["ties"],
            opponents=len(tally[model]["opponents"]),
        )
        for model in models
    ]
    ratings.sort(key=lambda r: -r.elo)
    return Fit(ratings, math.exp(lam), gamma, current, iterations, converged)


def _tally(comparisons: Sequence[Comparison]) -> dict:
    out: dict = defaultdict(
        lambda: {"games": 0, "wins": 0.0, "losses": 0.0, "ties": 0, "opponents": set()}
    )
    for c in comparisons:
        for me, them, sign in (
            (c.left, c.right, c.result),
            (c.right, c.left, -c.result),
        ):
            entry = out[me]
            entry["games"] += 1
            entry["opponents"].add(them)
            if sign == WIN:
                entry["wins"] += 1
            elif sign == TIE:
                entry["ties"] += 1
            else:
                entry["losses"] += 1
    return out


def bootstrap_ratings(
    comparisons: Sequence[Comparison],
    *,
    resamples: int = 300,
    seed: int = 0,
    alpha: float = 0.05,
    **fit_kwargs,
) -> Fit:
    """Cluster bootstrap over *batches*, not comparisons.

    Comparisons inside a batch are not independent -- one user decision
    generates all of them at once -- so resampling individual pairs would
    understate the uncertainty badly.
    """
    point = fit_davidson(comparisons, **fit_kwargs)
    if not point.ratings:
        return point

    by_batch: dict[str, list[Comparison]] = defaultdict(list)
    for c in comparisons:
        by_batch[c.batch_id].append(c)
    clusters = list(by_batch.values())

    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    bias_samples: list[float] = []
    for _ in range(resamples):
        drawn: list[Comparison] = []
        for _ in range(len(clusters)):
            drawn.extend(rng.choice(clusters))
        fit = fit_davidson(drawn, **fit_kwargs)
        bias_samples.append(fit.position_bias)
        for rating in fit.ratings:
            samples[rating.model].append(rating.elo)
    bias_samples.sort()
    bias_ci = (
        (
            bias_samples[int(alpha / 2 * len(bias_samples))],
            bias_samples[
                min(len(bias_samples) - 1, int((1 - alpha / 2) * len(bias_samples)))
            ],
        )
        if len(bias_samples) >= 10
        else None
    )

    ratings = []
    for rating in point.ratings:
        values = sorted(samples.get(rating.model, []))
        if len(values) < 10:
            ratings.append(rating)
            continue
        lo = values[int(alpha / 2 * len(values))]
        hi = values[min(len(values) - 1, int((1 - alpha / 2) * len(values)))]
        ratings.append(Rating(**{**rating.__dict__, "lo": lo, "hi": hi}))
    return Fit(
        ratings,
        point.tie_param,
        point.position_bias,
        point.log_likelihood,
        point.iterations,
        point.converged,
        position_bias_ci=bias_ci,
    )


def depth_buckets(
    comparisons: Sequence[Comparison], *, buckets: int = 3
) -> list[tuple[str, list[Comparison]]]:
    """Split comparisons into equal-count depth bands.

    This is the depth-conditional question: is a model good at opening a
    passage, or good at sustaining one that already has a voice? Batches at
    depth 3 and depth 80 are different tasks, and a single rating averages
    over that distinction rather than measuring it.
    """
    ordered = sorted(comparisons, key=lambda c: c.depth)
    if not ordered:
        return []
    size = max(1, len(ordered) // buckets)
    out = []
    for b in range(buckets):
        start = b * size
        end = len(ordered) if b == buckets - 1 else (b + 1) * size
        chunk = ordered[start:end]
        if not chunk:
            continue
        out.append((f"depth {chunk[0].depth}-{chunk[-1].depth}", chunk))
    return out


def rating_table(fit: Fit, *, shorten: bool = True) -> str:
    """Render a fit as a fixed-width table."""
    if not fit.ratings:
        return "(no comparisons)"
    name = (lambda m: m.split("/")[-1]) if shorten else (lambda m: m)
    width = max(len(name(r.model)) for r in fit.ratings)
    has_ci = any(r.lo is not None for r in fit.ratings)
    lines = []
    header = f"{'model':{width}s} {'elo':>7s}"
    if has_ci:
        header += f" {'95% CI':>17s}"
    header += f" {'n':>5s} {'W':>6s} {'T':>5s} {'L':>6s} {'opp':>4s}"
    lines.append(header)
    for r in fit.ratings:
        line = f"{name(r.model):{width}s} {r.elo:7.0f}"
        if has_ci:
            ci = f"[{r.lo:.0f}, {r.hi:.0f}]" if r.lo is not None else "-"
            line += f" {ci:>17s}"
        line += (
            f" {r.games:5d} {r.wins:6.0f} {r.ties:5d} {r.losses:6.0f} {r.opponents:4d}"
        )
        lines.append(line)
    lines.append(
        f"\ntie parameter nu={fit.tie_param:.2f}"
        + (
            f", position bias={fit.position_bias * ELO_PER_LOGIT:+.0f} elo/slot"
            + (
                f" [{fit.position_bias_ci[0] * ELO_PER_LOGIT:+.0f}, "
                f"{fit.position_bias_ci[1] * ELO_PER_LOGIT:+.0f}]"
                if fit.position_bias_ci
                else ""
            )
            if fit.position_bias
            else ""
        )
        + f", logL={fit.log_likelihood:.1f}"
        + ("" if fit.converged else "  [NOT CONVERGED]")
    )
    return "\n".join(lines)
