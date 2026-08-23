"""Application-level state for a loom session.

This is the shared interface between all UI layers (TUI, web backend). UI
layers interact with loom state only through LoomSession and never call
GenerationStore or generation functions directly.
"""

from __future__ import annotations

import asyncio
import difflib
import random
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any, Literal

from basemode.continue_ import continue_text
from basemode.detect import detect_strategy
from basemode.healing import normalize_completion_segment
from basemode.keys import get_default_model
from basemode.usage import estimate_usage

from .diagnostics import (
    ProviderDiagnostic,
    empty_response_diagnostic,
    provider_diagnostic,
)
from .logging_utils import get_logger
from .model_plan import MAX_MAX_TOKENS, MIN_MAX_TOKENS, ModelPlanEntry, parse_model_plan
from .model_resolver import resolve_model_id
from .naming import generate_name, should_name
from .store import GenerationStore, Node

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Events emitted during generate()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenReceived:
    model_idx: int
    branch_idx: int
    slot_idx: int
    token: str


@dataclass(frozen=True)
class GenerationComplete:
    completions: list[str]
    new_nodes: list[Node]


@dataclass(frozen=True)
class GenerationFailure:
    """A single failed member of a multi-model generation batch."""

    model: str
    model_idx: int
    branch_idx: int
    slot_idx: int
    incident_id: str | None = None
    category: str | None = None
    status: int | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class GenerationError:
    error: Exception
    incident_id: str | None = None
    category: str | None = None
    status: int | None = None
    finish_reason: str | None = None
    failures: tuple[GenerationFailure, ...] = ()


@dataclass(frozen=True)
class GenerationCancelled:
    pass


GenerationEvent = (
    TokenReceived | GenerationComplete | GenerationError | GenerationCancelled
)


@dataclass(frozen=True)
class PromptEntry:
    model: str
    strategy: str
    prefix: str
    messages: tuple[tuple[str, str], ...] | None


@dataclass(frozen=True)
class LineageSegment:
    """One node's contribution to the current path, plus its chat role (if any).

    Loom-shaped nodes have ``role=None``; chat nodes carry "user"/"assistant"/etc.
    Carrying segments (rather than only the flat ``full_text``) lets the display
    layer insert role headers at turn boundaries without changing stored text.
    """

    text: str
    role: str | None
    node_id: str


# ---------------------------------------------------------------------------
# State snapshot consumed by UI layers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionState:
    current_node_id: str
    current_node: Node
    full_text: str
    children: list[Node]
    selected_child_idx: int
    descendant_counts: dict[str, int]
    continuation_text: str  # text from selected child's checked-out subtree
    model: str
    max_tokens: int
    temperature: float
    n_branches: int
    context: str
    root_id: str
    global_max_tokens: int = 200
    global_n_branches: int = 1
    rewind_split_tokens: bool = False
    view_mode: Literal["branch", "tree"] = "branch"
    prompt_entries: tuple[PromptEntry, ...] = field(default_factory=tuple)
    hoisted_node_id: str | None = None
    tree_nodes: list[Node] | None = None
    show_model_names: bool = True
    render_chat_headers: bool = False
    lineage_segments: tuple[LineageSegment, ...] = field(default_factory=tuple)
    continuation_segments: tuple[LineageSegment, ...] = field(default_factory=tuple)
    model_plan: list[ModelPlanEntry] = field(default_factory=list)
    tree_prompt_tokens: int = 0
    tree_completion_tokens: int = 0
    tree_total_tokens: int = 0
    tree_cost_usd: float = 0.0
    tree_pricing_complete: bool = True


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class LoomSession:
    def __init__(self, store: GenerationStore, start_id: str) -> None:
        self._store = store
        self._cancelled = asyncio.Event()

        root_node = store.root(start_id)
        tree = store.tree_for_node(root_node.id)
        start_node = store.get(start_id)
        if start_node is not None and start_node.id != root_node.id:
            self._current_id = start_node.id
        elif tree.current_node_id and store.get(tree.current_node_id) is not None:
            self._current_id: str = tree.current_node_id
        else:
            self._current_id = start_id

        self._child_path: dict[str, int] = self._load_child_path(self._current_id)
        self._selected_idx: int = self._child_path.get(self._current_id, 0)

        if tree.model_plan:
            self._model_plan = self._parse_model_plan(tree.model_plan)
        else:
            self._model_plan = [
                ModelPlanEntry(
                    model=str(get_default_model() or "gpt-4o-mini"),
                    max_tokens=200,
                    temperature=0.9,
                    n_branches=1,
                    enabled=True,
                )
            ]

        self.rewind_split_tokens: int = tree.rewind_split_tokens
        self.global_max_tokens: int = tree.global_max_tokens
        self.global_n_branches: int = tree.global_n_branches
        self.view_mode: Literal["branch", "tree"] = "branch"
        self._hoisted_id: str | None = None
        self.show_model_names: bool = tree.show_model_names
        # A tree is "chat-shaped" if it mixes ≥2 distinct roles (e.g. user +
        # assistant). Pure loom trees have 0-or-1 role and stay header-free.
        self._is_chat_tree: bool = len(store.distinct_roles(self._current_id)) >= 2
        self.render_chat_headers: bool = True

    # --- State snapshot ---

    def get_state(self) -> SessionState:
        store = self._store
        node = store.get(self._current_id)
        assert node is not None
        children = store.children(self._current_id)
        selected_idx = min(self._selected_idx, max(0, len(children) - 1))
        counts = store.descendant_counts([c.id for c in children]) if children else {}
        root = store.root(self._current_id)
        continuation = (
            self._get_continuation_text(children[selected_idx]) if children else ""
        )
        continuation_segments = (
            self._get_continuation_segments(children[selected_idx]) if children else ()
        )
        tree_nodes = store.tree(root.id) if self.view_mode == "tree" else None
        (
            tree_prompt_tokens,
            tree_completion_tokens,
            tree_total_tokens,
            tree_cost_usd,
            tree_pricing_complete,
        ) = self._tree_usage(root.id, tree_nodes)
        lineage_segments = tuple(
            LineageSegment(
                text=n.text,
                role=_node_role(n),
                node_id=n.id,
            )
            for n in store.lineage(self._current_id)
            if n.kind != "context"
        )
        return SessionState(
            current_node_id=self._current_id,
            current_node=node,
            full_text=store.full_text(self._current_id),
            children=children,
            selected_child_idx=selected_idx,
            descendant_counts=counts,
            continuation_text=continuation,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            n_branches=self.n_branches,
            global_max_tokens=self.global_max_tokens,
            global_n_branches=self.global_n_branches,
            rewind_split_tokens=bool(self.rewind_split_tokens),
            model_plan=self.model_plan,
            context=self._current_context(node),
            root_id=root.id,
            view_mode=self.view_mode,
            hoisted_node_id=self._hoisted_id,
            tree_nodes=tree_nodes,
            show_model_names=self.show_model_names,
            render_chat_headers=self._is_chat_tree and self.render_chat_headers,
            lineage_segments=lineage_segments,
            continuation_segments=continuation_segments,
            tree_prompt_tokens=tree_prompt_tokens,
            tree_completion_tokens=tree_completion_tokens,
            tree_total_tokens=tree_total_tokens,
            tree_cost_usd=tree_cost_usd,
            tree_pricing_complete=tree_pricing_complete,
            prompt_entries=self._build_prompt_entries(
                store.full_text(self._current_id), self._current_context(node)
            ),
        )

    def _get_continuation_text(self, selected_child: Node) -> str:
        return "".join(
            seg.text for seg in self._get_continuation_segments(selected_child)
        )

    def _get_continuation_segments(
        self, selected_child: Node
    ) -> tuple[LineageSegment, ...]:
        """Walk the checked-out path below the selected child, carrying roles."""
        segments: list[LineageSegment] = []
        node = selected_child
        while True:
            deeper = self._store.children(node.id)
            if not deeper:
                break
            node = deeper[min(self._child_path.get(node.id, 0), len(deeper) - 1)]
            segments.append(
                LineageSegment(text=node.text, role=_node_role(node), node_id=node.id)
            )
        return tuple(segments)

    # --- Navigation ---

    def navigate_child(self) -> SessionState:
        children = self._store.children(self._current_id)
        if not children:
            return self.get_state()
        idx = min(self._selected_idx, len(children) - 1)
        self._store.set_checked_out_child(self._current_id, children[idx].id)
        self._child_path[self._current_id] = idx
        self._current_id = children[idx].id
        self._selected_idx = self._child_path.get(self._current_id, 0)
        return self.get_state()

    def navigate_parent(self) -> SessionState:
        node = self._store.get(self._current_id)
        if node is None or node.parent_id is None:
            return self.get_state()
        parent_id = node.parent_id
        siblings = self._store.children(parent_id)
        for i, c in enumerate(siblings):
            if c.id == self._current_id:
                self._selected_idx = i
                break
        else:
            self._selected_idx = self._child_path.get(parent_id, 0)
        self._current_id = parent_id
        return self.get_state()

    def checkout(self, node_id: str) -> SessionState:
        """Select a node from its parent and make its ancestry checked out."""
        node = self._store.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        if self._store.root(node.id).id != self._store.root(self._current_id).id:
            raise ValueError("node is outside this session's tree")

        lineage = self._store.lineage(node.id)
        for parent, child in pairwise(lineage):
            siblings = self._store.children(parent.id)
            self._store.set_checked_out_child(parent.id, child.id)
            self._child_path[parent.id] = siblings.index(child)

        # The reader presents a current node plus one highlighted child. Keep
        # the clicked node in that child position instead of descending into it.
        current = lineage[-2] if len(lineage) > 1 else node
        self._store.set_active_node(current.id)
        self._current_id = current.id
        self._child_path.update(self._load_child_path(current.id))
        self._selected_idx = self._child_path.get(current.id, 0)
        return self.get_state()

    def select_sibling(self, delta: int) -> SessionState:
        children = self._store.children(self._current_id)
        if not children:
            return self.get_state()
        new_idx = (self._selected_idx + delta) % len(children)
        if new_idx != self._selected_idx:
            self._selected_idx = new_idx
            self._store.set_checked_out_child(self._current_id, children[new_idx].id)
            self._child_path[self._current_id] = new_idx
        return self.get_state()

    def toggle_tree_view(self) -> SessionState:
        self.view_mode = "tree" if self.view_mode == "branch" else "branch"
        return self.get_state()

    def toggle_model_names(self) -> SessionState:
        self.show_model_names = not self.show_model_names
        return self.get_state()

    def toggle_chat_headers(self) -> SessionState:
        self.render_chat_headers = not self.render_chat_headers
        return self.get_state()

    def toggle_hoist(self) -> SessionState:
        self._hoisted_id = None if self._hoisted_id else self._current_id
        return self.get_state()

    def toggle_bookmark(self) -> bool:
        node = self._store.get(self._current_id)
        if node is None:
            return False
        bookmarked = not bool(node.metadata.get("bookmarked"))
        self._store.update_metadata(node.id, {"bookmarked": bookmarked})
        return bookmarked

    def next_bookmark(self) -> SessionState:
        root = self._store.root(self._current_id)
        nodes = self._store.tree(root.id)
        bookmarked = [node for node in nodes if node.metadata.get("bookmarked")]
        if not bookmarked:
            return self.get_state()

        ids = [node.id for node in nodes]
        try:
            current_pos = ids.index(self._current_id)
        except ValueError:
            current_pos = -1

        ordered = [node for node in bookmarked if ids.index(node.id) > current_pos]
        ordered.extend(node for node in bookmarked if ids.index(node.id) <= current_pos)
        self._checkout_node(ordered[0].id)
        return self.get_state()

    # --- Generation ---

    def cancel(self) -> None:
        self._cancelled.set()

    async def generate(
        self, *, max_context_tokens: int | None = None
    ) -> AsyncGenerator[GenerationEvent, None]:
        self._cancelled.clear()
        state = self.get_state()
        source_node_id = state.current_node_id
        prefix = state.full_text
        context = state.context

        branch_plan: list[tuple[int, int, ModelPlanEntry]] = []
        for model_idx, plan in enumerate(self._model_plan):
            if not plan.enabled:
                continue
            effective_plan = (
                replace(
                    plan,
                    n_branches=self.global_n_branches,
                    max_tokens=self.global_max_tokens,
                )
                if plan.pinned_settings
                else plan
            )
            for branch_idx in range(effective_plan.n_branches):
                branch_plan.append((model_idx, branch_idx, effective_plan))
        random.shuffle(branch_plan)

        if not branch_plan:
            yield GenerationError(error=RuntimeError("no enabled model branches"))
            return

        if max_context_tokens is not None:
            for _model_idx, _branch_idx, plan in branch_plan:
                resolved_model = resolve_model_id(plan.model)
                strategy = detect_strategy(resolved_model, None).name
                prompt, messages = _usage_prompt(
                    resolved_model, prefix, strategy, context
                )
                usage = estimate_usage(
                    resolved_model,
                    prompt,
                    "",
                    prompt_messages=messages,
                )
                if usage.prompt_tokens > max_context_tokens:
                    yield GenerationError(
                        error=RuntimeError("assembled prompt exceeds context limit")
                    )
                    return

        buffers: list[list[str]] = [[] for _ in range(len(branch_plan))]
        branch_errors: dict[int, ProviderDiagnostic] = {}
        cancelled = False

        queue: asyncio.Queue[
            tuple[str, int, int, int, str | ProviderDiagnostic | None]
        ] = asyncio.Queue()

        async def run_branch(
            slot_idx: int, model_idx: int, branch_idx: int, plan: ModelPlanEntry
        ) -> None:
            try:
                async for tok in continue_text(
                    prefix,
                    resolve_model_id(plan.model),
                    max_tokens=plan.max_tokens,
                    temperature=plan.temperature,
                    context=context,
                    rewind=bool(self.rewind_split_tokens),
                    strict_max_tokens=True,
                ):
                    if self._cancelled.is_set():
                        break
                    await queue.put(("token", slot_idx, model_idx, branch_idx, tok))
            except Exception as exc:
                diagnostic = provider_diagnostic(exc)
                log.error(
                    "generation branch failed "
                    f"model={plan.model} model_idx={model_idx} "
                    f"branch_idx={branch_idx} slot_idx={slot_idx} "
                    f"category={diagnostic.category} status={diagnostic.status} "
                    f"finish_reason={diagnostic.finish_reason} "
                    f"incident_id={diagnostic.incident_id}"
                )
                await queue.put(("error", slot_idx, model_idx, branch_idx, diagnostic))
            finally:
                await queue.put(("done", slot_idx, model_idx, branch_idx, None))

        tasks = [
            asyncio.create_task(run_branch(slot_idx, model_idx, branch_idx, plan))
            for slot_idx, (model_idx, branch_idx, plan) in enumerate(branch_plan)
        ]
        try:
            done = 0
            while done < len(tasks):
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    if self._cancelled.is_set():
                        cancelled = True
                        break
                    continue
                kind, slot_idx, model_idx, branch_idx, payload = item
                if kind == "done":
                    done += 1
                elif kind == "error":
                    assert isinstance(payload, ProviderDiagnostic)
                    branch_errors[slot_idx] = payload
                else:
                    tok = str(payload)
                    buffers[slot_idx].append(tok)
                    yield TokenReceived(
                        model_idx=model_idx,
                        branch_idx=branch_idx,
                        slot_idx=slot_idx,
                        token=tok,
                    )

                if self._cancelled.is_set():
                    cancelled = True
                    break
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if cancelled:
            yield GenerationCancelled()
            return

        successful: list[tuple[tuple[int, int, ModelPlanEntry], str]] = []
        for slot_idx, plan_entry in enumerate(branch_plan):
            if slot_idx in branch_errors:
                continue
            raw = "".join(buffers[slot_idx])
            if not normalize_completion_segment(prefix, raw).strip():
                _model_idx, _branch_idx, plan = plan_entry
                log.warning(
                    "generation branch produced empty completion "
                    f"source_node={source_node_id} model={plan.model} "
                    f"model_idx={_model_idx} branch_idx={_branch_idx} "
                    f"slot_idx={slot_idx}"
                )
                branch_errors[slot_idx] = empty_response_diagnostic()
                continue
            successful.append((plan_entry, raw))

        new_nodes: list[Node] = []
        if successful:
            plans = [entry[0] for entry in successful]
            completions = [entry[1] for entry in successful]
            new_nodes = self._save_completions(
                prefix, plans, completions, parent_id=source_node_id
            )
            log.info(
                "generation complete "
                f"source_node={source_node_id} "
                f"saved={len(new_nodes)} failed={len(branch_errors)}"
            )
            yield GenerationComplete(completions=completions, new_nodes=new_nodes)

        if branch_errors:
            first = next(iter(branch_errors.values()))
            count = len(branch_errors)
            message = f"{count} provider branch{'es' if count != 1 else ''} failed"
            log.warning(
                "generation partial failure "
                f"source_node={source_node_id} failed={count}"
            )
            yield GenerationError(
                error=RuntimeError(message),
                incident_id=first.incident_id,
                category=first.category,
                status=first.status,
                finish_reason=first.finish_reason,
                failures=tuple(
                    GenerationFailure(
                        model=branch_plan[slot_idx][2].model,
                        model_idx=branch_plan[slot_idx][0],
                        branch_idx=branch_plan[slot_idx][1],
                        slot_idx=slot_idx,
                        incident_id=diagnostic.incident_id,
                        category=diagnostic.category,
                        status=diagnostic.status,
                        finish_reason=diagnostic.finish_reason,
                    )
                    for slot_idx, diagnostic in sorted(branch_errors.items())
                ),
            )

    def _save_completions(
        self,
        prefix: str,
        branch_plan: list[tuple[int, int, ModelPlanEntry]],
        completions: list[str],
        *,
        parent_id: str,
    ) -> list[Node]:
        new_children: list[Node] = []
        for (model_idx, branch_idx, plan), completion in zip(
            branch_plan, completions, strict=False
        ):
            resolved = resolve_model_id(plan.model)
            strategy_name = detect_strategy(resolved, None).name
            normalized = normalize_completion_segment(prefix, completion)
            usage = self._estimate_usage(
                resolved,
                strategy_name,
                prefix,
                normalized,
            )
            node = self._store.add_child(
                parent_id,
                normalized,
                model=resolved,
                strategy=strategy_name,
                max_tokens=plan.max_tokens,
                temperature=plan.temperature,
                metadata={
                    "model_idx": model_idx,
                    "model_branch_index": branch_idx,
                    "usage": usage,
                },
            )
            new_children.append(node)

        if new_children:
            self._child_path[parent_id] = len(new_children) - 1
            self._store.set_checked_out_child(parent_id, new_children[-1].id)
            if len(new_children) == 1 and self._current_id == parent_id:
                self._current_id = new_children[0].id
                self._selected_idx = 0
        self._maybe_name_tree(new_children)
        return new_children

    def _maybe_name_tree(self, children: list[Node]) -> None:
        if not children:
            return
        root = self._store.root(children[0].id)
        tree = self._store.tree_for_node(root.id)
        if tree.name:
            return
        candidates = [(child, self._store.full_text(child.id)) for child in children]
        child, text = max(candidates, key=lambda item: len(item[1]))
        if not should_name(text):
            return
        name = generate_name(text)
        if name is None:
            return
        self._store.update_tree_settings(
            root.tree_id,
            name=name,
            metadata={"named_from": child.id},
        )

    # --- Editing ---

    def apply_edit(self, original: str, edited: str) -> Node | None:
        """Rewrite the current lineage to ``edited``, forking where it changed.

        Each node whose own text the edit touched is replaced by a new sibling;
        everything below the last of those -- the rest of the lineage and every
        other branch hanging off it -- is moved onto the rewritten node, so the
        pre-edit node is left holding just its old text. Returns the node the
        session now sits on, whose full text is ``edited``.
        """
        if original == edited:
            return None

        lineage = self._store.lineage(self._current_id)
        seg_starts: list[int] = []
        pos = 0
        for node in lineage:
            seg_starts.append(pos)
            pos += len(node.text)
        seg_starts.append(pos)

        opcodes = difflib.SequenceMatcher(
            None, original, edited, autojunk=False
        ).get_opcodes()
        changes = [op for op in opcodes if op[0] != "equal"]
        if not changes:
            return None

        first_change = changes[0][1]
        fork_idx = len(lineage) - 1
        for idx in range(len(lineage)):
            if first_change < seg_starts[idx + 1]:
                fork_idx = idx
                break

        boundaries = set(seg_starts[fork_idx:])
        edit_pos_of: dict[int, int] = {}
        for tag, i1, i2, j1, j2 in opcodes:
            for b in boundaries:
                if b in edit_pos_of:
                    continue
                if tag == "equal" and i1 <= b <= i2:
                    edit_pos_of[b] = j1 + (b - i1)
                elif tag in ("replace", "delete"):
                    if b == i1:
                        edit_pos_of[b] = j1
                    elif i1 < b <= i2:
                        edit_pos_of[b] = j2
        edit_pos_of[len(original)] = len(edited)
        for b in boundaries:
            edit_pos_of.setdefault(b, b)

        def segment_of(idx: int) -> str:
            return edited[
                edit_pos_of[seg_starts[idx]] : edit_pos_of[seg_starts[idx + 1]]
            ]

        # Only the nodes whose own text actually changed need rewriting. Every
        # node below the last of those is untouched by the edit, so it keeps
        # its identity and rides along on the moved subtree instead of being
        # duplicated onto the new branch.
        last_changed_idx = fork_idx
        for idx in range(fork_idx, len(lineage)):
            if segment_of(idx) != lineage[idx].text:
                last_changed_idx = idx

        prev_parent_id: str | None = lineage[fork_idx].parent_id
        last_new_node: Node | None = None

        for idx in range(fork_idx, last_changed_idx + 1):
            node = lineage[idx]
            new_seg = segment_of(idx)
            if node.parent_id is None:
                # A root has no sibling position to fork into: create_root
                # would start a whole new tree and leave every branch behind on
                # the old one, so a root's text is rewritten in place.
                new_node = self._store.update_text(node.id, new_seg)
            else:
                new_node = self._store.add_child(
                    prev_parent_id,  # type: ignore[arg-type]
                    new_seg,
                    model=node.model or "manual",
                    strategy=node.strategy or "manual",
                    max_tokens=node.max_tokens or 200,
                    temperature=node.temperature or 0.9,
                )
            prev_parent_id = new_node.id
            last_new_node = new_node

        if last_new_node is None:
            return None

        # Everything hanging off the last rewritten node -- the rest of the
        # lineage plus every other branch -- moves across, so the edited node
        # keeps its descendants and the pre-edit node is left holding only its
        # old text.
        self._store.move_children(lineage[last_changed_idx].id, last_new_node.id)

        # The current node only moves if it was itself rewritten; otherwise it
        # came along untouched under the new branch.
        current = (
            last_new_node
            if last_changed_idx == len(lineage) - 1
            else lineage[-1]
        )

        # Point the checked-out path at the new chain; without this the store
        # still walks down the pre-edit branch and the edit looks like it was
        # dropped as soon as anything re-derives the path from those flags.
        for parent, child in pairwise(self._store.lineage(current.id)):
            siblings = self._store.children(parent.id)
            self._store.set_checked_out_child(parent.id, child.id)
            self._child_path[parent.id] = siblings.index(child)

        self._store.set_active_node(current.id)
        self._current_id = current.id
        self._child_path.update(self._load_child_path(self._current_id))
        self._selected_idx = self._child_path.get(self._current_id, 0)
        return current

    def truncate_selected_child(self, char_pos: int) -> Node | None:
        """Create a sibling with the selected child's text truncated at char_pos and navigate into it."""
        children = self._store.children(self._current_id)
        if not children:
            return None
        selected = children[min(self._selected_idx, len(children) - 1)]
        truncated = selected.text[:char_pos]
        if not truncated or truncated == selected.text:
            return None
        new_node = self._store.add_child(
            self._current_id,
            truncated,
            model=selected.model or "manual",
            strategy=selected.strategy or "manual",
            max_tokens=selected.max_tokens or self.max_tokens,
            temperature=selected.temperature or self.temperature,
        )
        siblings = self._store.children(self._current_id)
        for i, c in enumerate(siblings):
            if c.id == new_node.id:
                self._selected_idx = i
                break
        self._store.set_checked_out_child(self._current_id, new_node.id)
        self._child_path[self._current_id] = self._selected_idx
        self._current_id = new_node.id
        self._selected_idx = 0
        return new_node

    def delete_selected_child(self) -> bool:
        children = self._store.children(self._current_id)
        if not children:
            return False
        selected_idx = min(self._selected_idx, len(children) - 1)
        selected = children[selected_idx]
        deleted = self._store.delete_subtree(selected.id)
        if deleted <= 0:
            return False

        updated = self._store.children(self._current_id)
        if not updated:
            self._selected_idx = 0
            self._child_path.pop(self._current_id, None)
            return True

        self._selected_idx = min(selected_idx, len(updated) - 1)
        self._child_path[self._current_id] = self._selected_idx
        self._store.set_checked_out_child(
            self._current_id, updated[self._selected_idx].id
        )
        return True

    def edit_node_text(self, node_id: str, new_text: str) -> Node | None:
        """Edit a single node segment by creating a direct forked node."""
        node = self._store.get(node_id)
        if node is None:
            return None
        if node.text == new_text:
            return None
        if node.parent_id is None:
            # See apply_edit: forking a root would strand the tree behind it.
            new_node = self._store.update_text(node.id, new_text)
        else:
            new_node = self._store.add_child(
                node.parent_id,
                new_text,
                model=node.model or "manual",
                strategy=node.strategy or "manual",
                max_tokens=node.max_tokens or self.max_tokens,
                temperature=node.temperature or self.temperature,
            )
        self._checkout_node(new_node.id)
        return new_node

    def update_context(self, context: str) -> None:
        node = self._store.get(self._current_id)
        if node is None:
            return
        if context:
            context_node = self._store.create_context(node.tree_id, context)
            self._store.set_node_context(node.id, context_node.id)
        else:
            self._store.set_node_context(node.id, None)

    def apply_config_patch(self, config_patch: dict[str, Any]) -> None:
        if "model_plan" in config_patch:
            self.set_model_plan(config_patch["model_plan"])
        if "model" in config_patch:
            self.set_model(str(config_patch["model"]))
        if "max_tokens" in config_patch:
            self.set_max_tokens(int(config_patch["max_tokens"]))
        if "temperature" in config_patch:
            self.temperature = float(config_patch["temperature"])
        if "n_branches" in config_patch:
            self.set_n_branches(int(config_patch["n_branches"]))
        if "global_max_tokens" in config_patch:
            self.global_max_tokens = max(
                MIN_MAX_TOKENS,
                min(int(config_patch["global_max_tokens"]), MAX_MAX_TOKENS),
            )
        if "global_n_branches" in config_patch:
            self.global_n_branches = max(1, min(int(config_patch["global_n_branches"]), 64))
        if "rewind_split_tokens" in config_patch:
            self.rewind_split_tokens = int(bool(config_patch["rewind_split_tokens"]))
        if "show_model_names" in config_patch:
            self.show_model_names = bool(config_patch["show_model_names"])
        if "context" in config_patch:
            self.update_context(str(config_patch["context"]))

    def persist_config(self, *, context: str | None = None) -> None:
        if context is not None:
            self.update_context(context)
        self._persist_tree_settings()

    # --- Params ---

    def set_model(self, model: str) -> None:
        if not self._model_plan:
            return
        p = self._model_plan[0]
        self._model_plan[0] = ModelPlanEntry(
            model=model,
            n_branches=p.n_branches,
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            enabled=p.enabled,
            pinned_settings=p.pinned_settings,
        )

    def set_max_tokens(self, max_tokens: int) -> None:
        if not self._model_plan:
            return
        p = self._model_plan[0]
        self._model_plan[0] = ModelPlanEntry(
            model=p.model,
            n_branches=p.n_branches,
            max_tokens=max(MIN_MAX_TOKENS, min(max_tokens, MAX_MAX_TOKENS)),
            temperature=p.temperature,
            enabled=p.enabled,
            pinned_settings=p.pinned_settings,
        )

    def set_n_branches(self, n: int) -> None:
        if not self._model_plan:
            return
        per_model = max(1, n)
        self._model_plan = [
            ModelPlanEntry(
                model=p.model,
                n_branches=per_model,
                max_tokens=p.max_tokens,
                temperature=p.temperature,
                enabled=p.enabled,
                pinned_settings=p.pinned_settings,
            )
            for p in self._model_plan
        ]

    def set_model_plan(self, model_plan: list[dict]) -> None:
        parsed = self._parse_model_plan(model_plan)
        if parsed:
            self._model_plan = parsed

    # --- Persistence ---

    def save(self) -> None:
        self._store.set_active_node(self._current_id)
        self._persist_tree_settings()

    @property
    def store(self) -> GenerationStore:
        return self._store

    # --- Internal helpers ---

    def _load_child_path(self, current_id: str) -> dict[str, int]:
        child_path: dict[str, int] = {}
        node_id = current_id
        while True:
            children = self._store.children(node_id)
            if not children:
                break
            checked_id = self._store.get_checked_out_child_id(node_id)
            idx = 0
            if checked_id:
                for i, c in enumerate(children):
                    if c.id == checked_id:
                        idx = i
                        break
            child_path[node_id] = idx
            node_id = children[idx].id
        return child_path

    def _checkout_node(self, node_id: str) -> None:
        node = self._store.get(node_id)
        if node is None:
            return
        if node.parent_id:
            siblings = self._store.children(node.parent_id)
            for index, sibling in enumerate(siblings):
                if sibling.id == node.id:
                    self._store.set_checked_out_child(node.parent_id, node.id)
                    self._child_path[node.parent_id] = index
                    break
        self._store.set_active_node(node.id)
        self._current_id = node.id
        self._child_path.update(self._load_child_path(self._current_id))
        self._selected_idx = self._child_path.get(self._current_id, 0)

    def _parse_model_plan(self, raw_plan: list[dict]) -> list[ModelPlanEntry]:
        return parse_model_plan(raw_plan)

    def _estimate_usage(
        self, model: str, strategy: str, prefix: str, completion: str
    ) -> dict[str, Any]:
        try:
            prompt, messages = _usage_prompt(model, prefix, strategy)
            usage = estimate_usage(
                model,
                prompt,
                completion,
                prompt_messages=messages,
                prompt_requests=1,
            )
        except Exception:
            return {}
        return {
            "model": usage.model,
            "prompt_tokens": int(usage.prompt_tokens),
            "completion_tokens": int(usage.completion_tokens),
            "total_tokens": int(usage.total_tokens),
            "cost_usd": float(usage.cost_usd or 0.0),
            "pricing_available": bool(usage.pricing_available),
        }

    def _tree_usage(
        self, node_id: str, tree_nodes: list[Node] | None = None
    ) -> tuple[int, int, int, float, bool]:
        nodes = tree_nodes if tree_nodes is not None else self._store.tree(node_id)
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        cost_usd = 0.0
        pricing_complete = True

        for node in nodes:
            usage = node.metadata.get("usage")
            if not isinstance(usage, dict):
                continue

            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            total_tokens += int(usage.get("total_tokens", 0) or 0)
            raw_cost = usage.get("cost_usd")
            if isinstance(raw_cost, (int, float)):
                cost_usd += float(raw_cost)
            elif usage:
                pricing_complete = False
            if usage.get("pricing_available") is False:
                pricing_complete = False

        return (
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost_usd,
            pricing_complete,
        )

    def _build_prompt_entries(
        self, prefix: str, context: str
    ) -> tuple[PromptEntry, ...]:
        entries = []
        for plan in self._model_plan:
            if not plan.enabled:
                continue
            model_id = resolve_model_id(plan.model)
            strategy = detect_strategy(model_id, None).name
            raw_prefix, raw_messages = _usage_prompt(
                model_id, prefix, strategy, context
            )
            messages = (
                tuple((m["role"], m["content"]) for m in raw_messages)
                if raw_messages is not None
                else None
            )
            entries.append(
                PromptEntry(
                    model=plan.model,
                    strategy=strategy,
                    prefix=raw_prefix,
                    messages=messages,
                )
            )
        return tuple(entries)

    def _current_context(self, node: Node) -> str:
        for ancestor in reversed(self._store.lineage(node.id)):
            if ancestor.context_id:
                context = self._store.get(ancestor.context_id)
                if context is not None and context.kind == "context":
                    return context.text
        return ""

    def _persist_tree_settings(self) -> None:
        root_node = self._store.root(self._current_id)
        self._store.update_tree_settings(
            root_node.tree_id,
            show_model_names=self.show_model_names,
            rewind_split_tokens=self.rewind_split_tokens,
            global_max_tokens=self.global_max_tokens,
            global_n_branches=self.global_n_branches,
            model_plan=[p.as_dict() for p in self._model_plan],
        )

    @property
    def model_plan(self) -> list[ModelPlanEntry]:
        return list(self._model_plan)

    @property
    def model(self) -> str:
        for plan in self._model_plan:
            if plan.enabled:
                return plan.model
        return self._model_plan[0].model if self._model_plan else "gpt-4o-mini"

    @property
    def max_tokens(self) -> int:
        return self._model_plan[0].max_tokens if self._model_plan else 200

    @property
    def temperature(self) -> float:
        return self._model_plan[0].temperature if self._model_plan else 0.9

    @temperature.setter
    def temperature(self, value: float) -> None:
        if not self._model_plan:
            return
        p = self._model_plan[0]
        self._model_plan[0] = ModelPlanEntry(
            model=p.model,
            n_branches=p.n_branches,
            max_tokens=p.max_tokens,
            temperature=value,
            enabled=p.enabled,
            pinned_settings=p.pinned_settings,
        )

    @property
    def n_branches(self) -> int:
        return sum(p.n_branches for p in self._model_plan if p.enabled)

    @n_branches.setter
    def n_branches(self, value: int) -> None:
        self.set_n_branches(value)

    @property
    def branches_per_model(self) -> int:
        if not self._model_plan:
            return 1
        for plan in self._model_plan:
            if plan.enabled:
                return plan.n_branches
        return self._model_plan[0].n_branches


def _node_role(node: Node) -> str | None:
    role = node.metadata.get("role")
    return str(role) if isinstance(role, str) and role else None


def _usage_prompt(
    model: str, prefix: str, strategy: str, context: str = ""
) -> tuple[str, list[dict[str, str]] | None]:
    from basemode.healing import normalize_prefix
    from basemode.strategies.few_shot import _SYSTEM_PROMPT as FEW_SHOT_SYSTEM_PROMPT
    from basemode.strategies.fim import _fim_prompt
    from basemode.strategies.prefill import SEED_LEN
    from basemode.strategies.system import SYSTEM_PROMPT

    def _with_context(system: str) -> str:
        if context:
            return system + f"\n\n<CONTEXT>\n{context}\n</CONTEXT>"
        return system

    if strategy == "system":
        return "", [
            {"role": "system", "content": _with_context(SYSTEM_PROMPT)},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strategy == "few_shot":
        return "", [
            {"role": "system", "content": _with_context(FEW_SHOT_SYSTEM_PROMPT)},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strategy == "prefill":
        system = (
            "You are continuing the following text. "
            "Output only the continuation - no preamble, no commentary.\n\n"
            f"Text to continue:\n{prefix}"
        )
        seed = prefix[-SEED_LEN:] if len(prefix) > SEED_LEN else prefix
        return "", [
            {"role": "system", "content": _with_context(system)},
            {"role": "user", "content": "[continue]"},
            {"role": "assistant", "content": seed},
        ]
    if strategy == "fim":
        return _fim_prompt(prefix), None
    return normalize_prefix(prefix), None
