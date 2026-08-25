"""Model identity: which recorded strings name the same model.

The store keeps whatever model id the call was made with, so one model appears
as ``moonshot/kimi-k3`` and ``together_ai/moonshotai/kimi-k3``, and an older
era of a tree records a bare ``gpt-5.4`` where a newer one records
``openai/gpt-5.4``. Left alone a model splits into several thinly-sampled
ghosts, and one of them tops the table on a record of eight wins in ten games.

The merge rule is deliberately conservative: only exact leaf-name matches.
``deepseek-v4-flash`` and ``deepseek-v4-flash-0731`` stay apart, because a
dated pin really might be a different snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

#: Not models. ``manual`` is text the user typed; rating it against a model
#: would be rating the author against their tools.
NON_MODELS = frozenset({"manual", "user", "human", ""})


def leaf(model: str) -> str:
    """The model name without gateway or vendor prefix."""
    return model.rsplit("/", 1)[-1]


def gateway(model: str) -> str:
    """The prefix that was stripped, or an empty string."""
    return model.rsplit("/", 1)[0] if "/" in model else ""


def canonical(model: str) -> str:
    """Identity to rate a model under when gateway variants are merged."""
    return leaf(model)


def gateway_variants(models: Iterable[str]) -> dict[str, list[str]]:
    """Leaf name -> the several ids it was recorded under, where >1 exists."""
    groups: dict[str, list[str]] = defaultdict(list)
    for model in models:
        groups[leaf(model)].append(model)
    return {name: sorted(set(ids)) for name, ids in groups.items() if len(set(ids)) > 1}
