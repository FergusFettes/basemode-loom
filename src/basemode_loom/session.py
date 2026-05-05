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
from dataclasses import dataclass, field
from typing import Any, Literal

from basemode.continue_ import continue_text
from basemode.detect import detect_strategy
from basemode.healing import normalize_completion_segment
from basemode.keys import get_default_model
from basemode.usage import estimate_usage

from .logging_utils import get_logger
from .model_resolver import resolve_model_id
from .naming import generate_name, should_name
from .store import GenerationStore, Node

log = get_logger(__name__)


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
        self.view_mode: Literal["branch", "tree"] = "branch"
        self._hoisted_id: str | None = None
        self.show_model_names: bool = tree.show_model_names

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
        (
            tree_prompt_tokens,
            tree_completion_tokens,
            tree_total_tokens,
            tree_cost_usd,
            tree_pricing_complete,
        ) = self._tree_usage(root.id, tree_nodes)
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
            context=self._current_context(node),
            root_id=root.id,
            view_mode=self.view_mode,
            hoisted_node_id=self._hoisted_id,
            tree_nodes=tree_nodes,
            show_model_names=self.show_model_names,
            tree_prompt_tokens=tree_prompt_tokens,
            tree_completion_tokens=tree_completion_tokens,
            tree_total_tokens=tree_total_tokens,
            tree_cost_usd=tree_cost_usd,
            tree_pricing_complete=tree_pricing_complete,
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
        source_node_id = state.current_node_id
        prefix = state.full_text
        context = state.context

        branch_plan: list[tuple[int, int, ModelPlanEntry]] = []
        for model_idx, plan in enumerate(self._model_plan):
            if not plan.enabled:
                continue
            for branch_idx in range(plan.n_branches):
                branch_plan.append((model_idx, branch_idx, plan))
        random.shuffle(branch_plan)

        if not branch_plan:
            yield GenerationError(error=RuntimeError("no enabled model branches"))
            return

        buffers: list[list[str]] = [[] for _ in range(len(branch_plan))]
        branch_errors: dict[int, Exception] = {}
        cancelled = False

        queue: asyncio.Queue[tuple[str, int, int, int, str | Exception | None]] = (
            asyncio.Queue()
        )

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
                ):
                    if self._cancelled.is_set():
                        break
                    await queue.put(("token", slot_idx, model_idx, branch_idx, tok))
            except Exception as exc:
                log.exception(
                    "generation branch failed "
                    f"model={plan.model} model_idx={model_idx} "
                    f"branch_idx={branch_idx} slot_idx={slot_idx}"
                )
                await queue.put(("error", slot_idx, model_idx, branch_idx, exc))
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
                    assert isinstance(payload, Exception)
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
            successful.append((plan_entry, "".join(buffers[slot_idx])))

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
            message = (
                str(first)
                if count == 1
                else f"{count} branches failed; first error: {first}"
            )
            log.warning(
                "generation partial failure "
                f"source_node={source_node_id} failed={count} message={message}"
            )
            yield GenerationError(error=RuntimeError(message))

    def _save_completions(
        self,
        prefix: str,
        branch_plan: list[tuple[int, int, ModelPlanEntry]],
        completions: list[str],
        *,
        parent_id: str,
    ) -> list[Node]:
        new_children: list[Node] = []
        for global_idx, ((model_idx, branch_idx, plan), completion) in enumerate(
            zip(branch_plan, completions, strict=False)
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
                branch_index=global_idx,
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
            new_node = self._store.create_root(new_text, metadata={"source": "edited"})
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
        root_node = self._store.root(self._current_id)
        if context:
            context_node = self._store.create_context(root_node.tree_id, context)
            self._store.set_node_context(root_node.id, context_node.id)
        else:
            self._store.set_node_context(root_node.id, None)

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
        per_model = max(1, n)
        self._model_plan = [
            ModelPlanEntry(
                model=p.model,
                n_branches=per_model,
                max_tokens=p.max_tokens,
                temperature=p.temperature,
                enabled=p.enabled,
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
        self, root_id: str, tree_nodes: list[Node] | None = None
    ) -> tuple[int, int, int, float, bool]:
        nodes = tree_nodes if tree_nodes is not None else self._store.tree(root_id)
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

    def _current_context(self, node: Node) -> str:
        context_id = node.context_id
        if context_id is None:
            root = self._store.root(node.id)
            context_id = root.context_id
        if context_id:
            context = self._store.get(context_id)
            if context is not None and context.kind == "context":
                return context.text
        return ""

    def _persist_tree_settings(self) -> None:
        root_node = self._store.root(self._current_id)
        self._store.update_tree_settings(
            root_node.tree_id,
            show_model_names=self.show_model_names,
            rewind_split_tokens=self.rewind_split_tokens,
            model_plan=[
                {
                    "model": p.model,
                    "n_branches": p.n_branches,
                    "max_tokens": p.max_tokens,
                    "temperature": p.temperature,
                    "enabled": p.enabled,
                }
                for p in self._model_plan
            ],
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


def _usage_prompt(
    model: str, prefix: str, strategy: str
) -> tuple[str, list[dict[str, str]] | None]:
    from basemode.healing import normalize_prefix
    from basemode.strategies.few_shot import _SYSTEM_PROMPT as FEW_SHOT_SYSTEM_PROMPT
    from basemode.strategies.fim import _fim_prompt
    from basemode.strategies.prefill import SEED_LEN
    from basemode.strategies.system import SYSTEM_PROMPT

    if strategy == "system":
        return "", [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strategy == "few_shot":
        return "", [
            {"role": "system", "content": FEW_SHOT_SYSTEM_PROMPT},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strategy == "prefill":
        seed = prefix[-SEED_LEN:] if len(prefix) > SEED_LEN else prefix
        return "", [
            {
                "role": "system",
                "content": (
                    "You are continuing the following text. "
                    "Output only the continuation - no preamble, no commentary.\n\n"
                    f"Text to continue:\n{prefix}"
                ),
            },
            {"role": "user", "content": "[continue]"},
            {"role": "assistant", "content": seed},
        ]
    if strategy == "fim":
        return _fim_prompt(prefix), None
    return normalize_prefix(prefix), None
