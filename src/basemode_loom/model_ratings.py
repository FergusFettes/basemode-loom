"""Per-user thumbs up/down for models, for the HTTP API.

Ratings live in basemode's own config file (``~/.config/basemode/auth.json``)
via :mod:`basemode.keys`, alongside keys and pinned strategies, rather than in
this project's corpus database. They belong to the user, not to a tree: a
corpus can be swapped with ``--db``, exported, and shared, and a thumb should
survive all three and be visible to the ``basemode`` CLI and the loom TUI too.

A rating only reorders model listings — a thumbs-up model sorts to the top of
``GET /api/models``, a thumbs-down one to the bottom. Nothing is hidden and
nothing about generation changes.
"""

from __future__ import annotations

from basemode.keys import (
    RATING_DOWN,
    RATING_UP,
    get_model_rating,
    list_model_ratings,
    set_model_rating,
)

from .model_resolver import resolve_model_id

VALID_RATINGS = (RATING_UP, RATING_DOWN)


def is_valid_rating(rating: object) -> bool:
    """True for a thumb or for `None`, which clears one."""
    if rating is None:
        return True
    return (
        isinstance(rating, int)
        and not isinstance(rating, bool)
        and rating in VALID_RATINGS
    )


def list_ratings() -> dict[str, int]:
    return list_model_ratings()


def get_rating(model: str) -> int | None:
    return get_model_rating(resolve_model_id(model))


def set_rating(model: str, rating: int | None) -> tuple[str, int | None]:
    """Rate `model`, returning the resolved ID the thumb was stored under.

    The ID is normalized the same way generation normalizes it, so a rating
    set as `gpt-4o-mini` and one set as `openai/gpt-4o-mini` are one thumb.
    """
    resolved = resolve_model_id(model)
    set_model_rating(resolved, rating)
    return resolved, rating
