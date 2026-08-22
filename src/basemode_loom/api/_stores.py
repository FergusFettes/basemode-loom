"""Store selection interfaces for embedded multi-store API deployments."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from starlette.types import Scope

from ..store import GenerationStore

StoreResolver: TypeAlias = Callable[[Scope], GenerationStore]


def fixed_store(store: GenerationStore) -> StoreResolver:
    """Return a resolver that always selects ``store``.

    This keeps the standalone CLI server's one-database behaviour while giving
    an embedding application a single seam for selecting another store from a
    trusted ASGI scope.
    """

    def resolve(_scope: Scope) -> GenerationStore:
        return store

    return resolve
