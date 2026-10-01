"""Plain chat on loom's tree structure.

A chat is an ordinary loom tree with an explicit ``mode: "chat"`` flag in
the tree metadata. Each node is one turn, tagged ``metadata.role`` as
``user`` or ``assistant``; the tree's context node is the system prompt.
Several replies to one user turn are sibling assistant nodes, so regenerating
or asking several models at once is just branching.

Chats live in their own database by default (`default_chat_db_path`), so they
never become loom's active node or show up in its tree picker. Nothing here
changes how ordinary loom trees generate: replies go through basemode's
`chat_text`, never the continuation path.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model_resolver import resolve_model_id
from .store import GenerationStore, Node

#: Tree-metadata flag marking a tree as a chat.
CHAT_MODE = "chat"

#: Strategy recorded on assistant nodes; matches basemode's health ledger.
CHAT_STRATEGY = "chat"

USER = "user"
ASSISTANT = "assistant"

_NAME_CHARS = 60


def default_chat_db_path() -> Path:
    """Where chats are stored unless ``--db`` says otherwise.

    Beside loom's own database but separate from it, so chatting never moves
    loom's active node. ``BASEMODE_CHAT_DB`` overrides it.
    """
    if path := os.environ.get("BASEMODE_CHAT_DB"):
        return Path(path).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "basemode" / "chats.sqlite"


def is_chat_tree(store: GenerationStore, node_id: str) -> bool:
    return store.tree_for_node(node_id).metadata.get("mode") == CHAT_MODE


def node_role(node: Node) -> str | None:
    role = node.metadata.get("role")
    return role if isinstance(role, str) and role else None


def start_chat(store: GenerationStore, text: str, *, system: str | None = None) -> Node:
    """Create a chat tree whose root is the first user turn."""
    metadata: dict[str, Any] = {"role": USER}
    if system:
        metadata["context"] = system
    root = store.create_root(text, metadata=metadata)
    name = " ".join(text.split())[:_NAME_CHARS] or None
    store.update_tree_settings(root.tree_id, name=name, metadata={"mode": CHAT_MODE})
    store.set_active_node(root.id)
    return root


def add_user_turn(store: GenerationStore, parent_id: str, text: str) -> Node:
    """Add a user turn below ``parent_id`` (normally an assistant reply)."""
    node = store.add_child(
        parent_id,
        text,
        model="manual",
        strategy="manual",
        max_tokens=0,
        temperature=0.0,
        metadata={"role": USER},
    )
    _checkout(store, node)
    return node


def discard_turn(store: GenerationStore, node: Node) -> None:
    """Remove an unanswered user turn (the whole chat, if it was the first)."""
    store.delete_subtree(node.id)
    if node.parent_id is not None:
        store.set_active_node(node.parent_id)


def latest_chat_node(store: GenerationStore) -> Node | None:
    """The node the most recently touched chat tree is sitting on."""
    for root in sorted(
        store.roots(), key=lambda r: store.tree_for_node(r.id).updated_at, reverse=True
    ):
        if is_chat_tree(store, root.id):
            tree = store.tree_for_node(root.id)
            return store.get(tree.current_node_id or root.id)
    return None


def chat_messages(store: GenerationStore, node_id: str) -> list[dict[str, str]]:
    """The conversation up to and including ``node_id``, as chat messages.

    Consecutive nodes with the same role are one turn, the same grouping the
    loom display uses for role headers.
    """
    lineage = store.lineage(node_id)
    messages: list[dict[str, str]] = []
    system = _system_prompt(store, lineage)
    if system:
        messages.append({"role": "system", "content": system})
    for node in lineage:
        if node.kind == "context":
            continue
        role = node_role(node) or USER
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += node.text
        else:
            messages.append({"role": role, "content": node.text})
    return messages


@dataclass(frozen=True)
class ReplyFailure:
    model: str
    slot: int
    error: BaseException


async def reply(
    store: GenerationStore,
    node_id: str,
    models: list[str],
    *,
    n: int = 1,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    on_token: Callable[[int, str], None] | None = None,
) -> tuple[list[Node], list[ReplyFailure]]:
    """Generate ``n`` assistant replies per model to the turn at ``node_id``.

    Branches stream concurrently; ``on_token(slot, token)`` sees every token,
    with slots numbered model-major (``models[0]``'s branches first). Each
    finished reply is saved as an assistant child. The first saved reply is
    checked out and becomes the chat's active node.
    """
    from basemode import chat_text

    from .basemode_adapter import loom_observation

    messages = chat_messages(store, node_id)
    plan = [resolve_model_id(model) for model in models for _ in range(n)]
    buffers: list[list[str]] = [[] for _ in plan]
    usage_events: list[list[dict]] = [[] for _ in plan]
    timings: list[list[float | None]] = [[None, None, None] for _ in plan]

    async def run(slot: int, model: str) -> None:
        def capture_usage(events: list[dict]) -> None:
            usage_events[slot] = events

        timings[slot][0] = time.perf_counter()
        try:
            async for token in chat_text(
                messages,
                model,
                max_tokens=max_tokens,
                temperature=temperature,
                observation=loom_observation(),
                on_usage=capture_usage,
            ):
                if timings[slot][1] is None:
                    timings[slot][1] = time.perf_counter()
                buffers[slot].append(token)
                if on_token is not None:
                    on_token(slot, token)
        finally:
            timings[slot][2] = time.perf_counter()

    results = await asyncio.gather(
        *(run(slot, model) for slot, model in enumerate(plan)),
        return_exceptions=True,
    )

    saved: list[Node] = []
    failures: list[ReplyFailure] = []
    for slot, (model, result) in enumerate(zip(plan, results, strict=True)):
        text = "".join(buffers[slot])
        if isinstance(result, BaseException):
            failures.append(ReplyFailure(model=model, slot=slot, error=result))
            continue
        if not text.strip():
            failures.append(
                ReplyFailure(model=model, slot=slot, error=RuntimeError("empty reply"))
            )
            continue
        saved.append(
            store.add_child(
                node_id,
                text,
                model=model,
                strategy=CHAT_STRATEGY,
                max_tokens=max_tokens,
                temperature=temperature,
                metadata={
                    "role": ASSISTANT,
                    "model_branch_index": slot,
                    "usage": _usage(model, messages, text, usage_events[slot]),
                    "timing": _timing(timings[slot], usage_events[slot]),
                },
            )
        )
    if saved:
        _checkout(store, saved[0])
    return saved, failures


def _checkout(store: GenerationStore, node: Node) -> None:
    if node.parent_id is not None:
        store.set_checked_out_child(node.parent_id, node.id)
    store.set_active_node(node.id)


def _system_prompt(store: GenerationStore, lineage: list[Node]) -> str:
    for node in reversed(lineage):
        if node.context_id:
            context = store.get(node.context_id)
            if context is not None and context.kind == "context":
                return context.text
    return ""


def _usage(
    model: str, messages: list[dict], text: str, events: list[dict]
) -> dict[str, Any]:
    from basemode.usage import estimate_usage, usage_from_events

    try:
        usage = usage_from_events(model, events) if events else None
        is_estimate = usage is None
        if usage is None:
            usage = estimate_usage(model, "", text, prompt_messages=messages)
    except Exception:
        return {}
    return {
        "model": usage.model,
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
        "cost_usd": float(usage.cost_usd or 0.0),
        "pricing_available": bool(usage.pricing_available),
        "is_estimate": is_estimate,
    }


def _timing(marks: list[float | None], events: list[dict]) -> dict[str, Any]:
    from .session import _timing_metadata

    started, first, finished = marks
    completion_tokens = sum(int(e.get("completion_tokens") or 0) for e in events)
    return _timing_metadata(
        started_at=started,
        first_token_at=first,
        finished_at=finished,
        completion_tokens=completion_tokens,
    )
