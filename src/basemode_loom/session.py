"""Application-level state for a loom session.

This is the shared interface between all UI layers (TUI, web backend). UI
layers interact with loom state only through LoomSession and never call
GenerationStore or generation functions directly.
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Literal

from basemode.continue_ import continue_text
from basemode.detect import detect_strategy, normalize_model
from basemode.healing import normalize_completion_segment
from basemode.keys import get_default_model

from .naming import generate_name, should_name
from .store import GenerationStore, Node


@dataclass(frozen=True)
class ModelPlanEntry:
    model: str
    n_branches: int
    max_tokens: int
    temperature: float
    enabled: bool = True


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
class GenerationError:
    error: Exception


@dataclass(frozen=True)
class GenerationCancelled:
    pass


GenerationEvent = (
    TokenReceived | GenerationComplete | GenerationError | GenerationCancelled
)


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
    view_mode: Literal["branch", "tree"] = "branch"
    hoisted_node_id: str | None = None
    tree_nodes: list[Node] | None = None
    show_model_names: bool = True
    model_plan: list[ModelPlanEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class LoomSession:
    def __init__(self, store: GenerationStore, start_id: str) -> None:
        self._store = store
        self._cancelled = asyncio.Event()

        root_node = store.root(start_id)
        meta = root_node.metadata
        saved_id = meta.get("last_node_id")
        if saved_id and store.get(saved_id) is not None:
            self._current_id: str = saved_id
        else:
            self._current_id = start_id

        self._child_path: dict[str, int] = self._load_child_path(self._current_id)
        self._selected_idx: int = self._child_path.get(self._current_id, 0)

        if isinstance(meta.get("model_plan"), list) and meta["model_plan"]:
            self._model_plan = self._parse_model_plan(meta["model_plan"])
        else:
            self._model_plan = [
                ModelPlanEntry(
                    model=str(meta.get("model", get_default_model() or "gpt-4o-mini")),
                    max_tokens=max(50, min(int(meta.get("max_tokens", 200)), 8000)),
                    temperature=float(meta.get("temperature", 0.9)),
                    n_branches=max(1, int(meta.get("n_branches", 1))),
                    enabled=True,
                )
            ]

        self.rewind: bool = bool(meta.get("rewind", False))
        self.view_mode: Literal["branch", "tree"] = "branch"
        self._hoisted_id: str | None = None
        self.show_model_names: bool = bool(meta.get("show_model_names", True))

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
        tree_nodes = store.tree(root.id) if self.view_mode == "tree" else None
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
            model_plan=self.model_plan,
            context=root.metadata.get("context", ""),
            root_id=root.id,
            view_mode=self.view_mode,
            hoisted_node_id=self._hoisted_id,
            tree_nodes=tree_nodes,
            show_model_names=self.show_model_names,
        )

    def _get_continuation_text(self, selected_child: Node) -> str:
        path_text = ""
        node = selected_child
        while True:
            deeper = self._store.children(node.id)
            if not deeper:
                break
            node = deeper[min(self._child_path.get(node.id, 0), len(deeper) - 1)]
            path_text += node.text
        return path_text

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

    async def generate(self) -> AsyncGenerator[GenerationEvent, None]:
        self._cancelled.clear()
        state = self.get_state()
        prefix = state.full_text
        context = state.context

        branch_plan: list[tuple[int, int, ModelPlanEntry]] = []
        for model_idx, plan in enumerate(self._model_plan):
            if not plan.enabled:
                continue
            for branch_idx in range(plan.n_branches):
                branch_plan.append((model_idx, branch_idx, plan))

        if not branch_plan:
            yield GenerationError(error=RuntimeError("no enabled model branches"))
            return

        buffers: list[list[str]] = [[] for _ in range(len(branch_plan))]
        error: Exception | None = None
        cancelled = False

        queue: asyncio.Queue[tuple[int, int, int, str] | Exception | None] = (
            asyncio.Queue()
        )

        async def run_branch(
            slot_idx: int, model_idx: int, branch_idx: int, plan: ModelPlanEntry
        ) -> None:
            try:
                async for tok in continue_text(
                    prefix,
                    plan.model,
                    max_tokens=plan.max_tokens,
                    temperature=plan.temperature,
                    context=context,
                    rewind=self.rewind,
                ):
                    if self._cancelled.is_set():
                        break
                    await queue.put((slot_idx, model_idx, branch_idx, tok))
            except Exception as exc:
                await queue.put(exc)
            finally:
                await queue.put(None)

        tasks = [
            asyncio.create_task(run_branch(slot_idx, model_idx, branch_idx, plan))
            for slot_idx, (model_idx, branch_idx, plan) in enumerate(branch_plan)
        ]
        try:
            done = 0
            while done < len(tasks):
                item = await queue.get()
                if item is None:
                    done += 1
                elif isinstance(item, Exception):
                    error = item
                    done += 1
                else:
                    slot_idx, model_idx, branch_idx, tok = item
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

        if error is not None:
            yield GenerationError(error=error)
            return

        completions = ["".join(b) for b in buffers]
        new_nodes = self._save_completions(prefix, branch_plan, completions)
        yield GenerationComplete(completions=completions, new_nodes=new_nodes)

    def _save_completions(
        self,
        prefix: str,
        branch_plan: list[tuple[int, int, ModelPlanEntry]],
        completions: list[str],
    ) -> list[Node]:
        new_children: list[Node] = []
        for global_idx, ((model_idx, branch_idx, plan), completion) in enumerate(
            zip(branch_plan, completions, strict=False)
        ):
            resolved = normalize_model(plan.model)
            strategy_name = detect_strategy(resolved, None).name
            normalized = normalize_completion_segment(prefix, completion)
            node = self._store.add_child(
                self._current_id,
                normalized,
                model=resolved,
                strategy=strategy_name,
                max_tokens=plan.max_tokens,
                temperature=plan.temperature,
                branch_index=global_idx,
                metadata={
                    "model_idx": model_idx,
                    "model_branch_index": branch_idx,
                },
            )
            new_children.append(node)

        if new_children:
            self._child_path[self._current_id] = len(new_children) - 1
            self._store.set_checked_out_child(self._current_id, new_children[-1].id)
            if len(new_children) == 1:
                self._current_id = new_children[0].id
                self._selected_idx = 0
        self._maybe_name_tree(new_children)
        return new_children

    def _maybe_name_tree(self, children: list[Node]) -> None:
        if not children:
            return
        root = self._store.root(children[0].id)
        if root.metadata.get("name"):
            return
        candidates = [(child, self._store.full_text(child.id)) for child in children]
        child, text = max(candidates, key=lambda item: len(item[1]))
        if not should_name(text):
            return
        name = generate_name(text)
        if name is None:
            return
        self._store.update_metadata(root.id, {"name": name, "named_from": child.id})

    # --- Editing ---

    def apply_edit(self, original: str, edited: str) -> Node | None:
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

        prev_parent_id: str | None = lineage[fork_idx].parent_id
        last_new_node: Node | None = None

        for idx in range(fork_idx, len(lineage)):
            node = lineage[idx]
            new_seg = edited[
                edit_pos_of[seg_starts[idx]] : edit_pos_of[seg_starts[idx + 1]]
            ]
            if node.parent_id is None:
                new_node = self._store.create_root(
                    new_seg, metadata={"source": "edited"}
                )
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

        if last_new_node:
            self._store.set_active_node(last_new_node.id)
            self._current_id = last_new_node.id
            self._selected_idx = 0
            self._child_path = self._load_child_path(self._current_id)
        return last_new_node

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

    def update_context(self, context: str) -> None:
        root = self._store.root(self._current_id)
        self._store.update_metadata(root.id, {"context": context})

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
        )

    def set_max_tokens(self, max_tokens: int) -> None:
        if not self._model_plan:
            return
        p = self._model_plan[0]
        self._model_plan[0] = ModelPlanEntry(
            model=p.model,
            n_branches=p.n_branches,
            max_tokens=max(50, min(max_tokens, 8000)),
            temperature=p.temperature,
            enabled=p.enabled,
        )

    def set_n_branches(self, n: int) -> None:
        if not self._model_plan:
            return
        p = self._model_plan[0]
        self._model_plan[0] = ModelPlanEntry(
            model=p.model,
            n_branches=max(1, n),
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            enabled=p.enabled,
        )

    def set_model_plan(self, model_plan: list[dict]) -> None:
        parsed = self._parse_model_plan(model_plan)
        if parsed:
            self._model_plan = parsed

    # --- Persistence ---

    def save(self) -> None:
        root_node = self._store.root(self._current_id)
        self._store.set_active_node(self._current_id)
        self._store.update_metadata(
            root_node.id,
            {
                "last_node_id": self._current_id,
                "model": self.model,
                "max_tokens": self.max_tokens,
                "n_branches": self.n_branches,
                "model_plan": [
                    {
                        "model": p.model,
                        "n_branches": p.n_branches,
                        "max_tokens": p.max_tokens,
                        "temperature": p.temperature,
                        "enabled": p.enabled,
                    }
                    for p in self._model_plan
                ],
                "rewind": self.rewind,
                "show_model_names": self.show_model_names,
            },
        )

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
        parsed: list[ModelPlanEntry] = []
        for raw in raw_plan:
            model = str(raw.get("model", "")).strip()
            if not model:
                continue
            parsed.append(
                ModelPlanEntry(
                    model=model,
                    n_branches=max(1, int(raw.get("n_branches", 1))),
                    max_tokens=max(50, min(int(raw.get("max_tokens", 200)), 8000)),
                    temperature=float(raw.get("temperature", 0.9)),
                    enabled=bool(raw.get("enabled", True)),
                )
            )
        return parsed

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
        )

    @property
    def n_branches(self) -> int:
        return sum(p.n_branches for p in self._model_plan if p.enabled)

    @n_branches.setter
    def n_branches(self, value: int) -> None:
        self.set_n_branches(value)
