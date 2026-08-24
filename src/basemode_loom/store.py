"""SQLite persistence for loom-style continuation trees."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import _schema
from .model_plan import normalize_model_plan

_CONFIG_METADATA_KEYS = {
    "context",
    "global_max_tokens",
    "global_n_branches",
    "max_tokens",
    "model",
    "model_plan",
    "n_branches",
    "rewind",
    "rewind_split_tokens",
    "show_model_names",
    "temperature",
}

# A tree that has never had a plan configured follows the global
# branches/tokens until the user unpins it.
_DEFAULT_MODEL_PLAN = [
    {
        "model": "gpt-4o-mini",
        "n_branches": 1,
        "max_tokens": 200,
        "temperature": 0.9,
        "enabled": True,
        "pinned_settings": True,
    }
]


def default_db_path() -> Path:
    """Return the default generation database path."""
    if path := os.environ.get("BASEMODE_DB"):
        return Path(path).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "basemode" / "generations.sqlite"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_root_metadata_config(metadata: dict[str, Any]) -> dict[str, Any]:
    existing_config = metadata.get("config")
    config = existing_config if isinstance(existing_config, dict) else {}

    normalized = {
        key: value
        for key, value in metadata.items()
        if key not in _CONFIG_METADATA_KEYS and key != "config"
    }

    model_plan = normalize_model_plan(config.get("model_plan"))
    if not model_plan:
        model_plan = _model_plan_from_legacy(metadata, config)

    new_config: dict[str, Any] = {}
    context = config.get("context", metadata.get("context"))
    if isinstance(context, str):
        new_config["context"] = context

    show_model_names = config.get("show_model_names", metadata.get("show_model_names"))
    if isinstance(show_model_names, bool):
        new_config["show_model_names"] = show_model_names

    for key in ("global_max_tokens", "global_n_branches"):
        value = config.get(key, metadata.get(key))
        if isinstance(value, int) and not isinstance(value, bool):
            new_config[key] = value

    if model_plan:
        new_config["model_plan"] = model_plan

    if new_config:
        normalized["config"] = new_config
    return normalized


def _model_plan_from_legacy(
    metadata: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    model = str(config.get("model", metadata.get("model", ""))).strip()
    if not model:
        return []
    return [
        {
            "model": model,
            "n_branches": max(
                1, int(config.get("n_branches", metadata.get("n_branches", 1)))
            ),
            "max_tokens": max(
                50,
                min(
                    int(config.get("max_tokens", metadata.get("max_tokens", 200))), 8000
                ),
            ),
            "temperature": float(
                config.get("temperature", metadata.get("temperature", 0.9))
            ),
            "enabled": True,
        }
    ]


def _tree_settings_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    config = metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
    model_plan = normalize_model_plan(config.get("model_plan"))
    if not model_plan:
        model_plan = _model_plan_from_legacy(metadata, config)
    if not model_plan:
        model_plan = _DEFAULT_MODEL_PLAN

    show_model_names = config.get(
        "show_model_names", metadata.get("show_model_names", True)
    )
    rewind_split_tokens = config.get(
        "rewind_split_tokens",
        metadata.get("rewind_split_tokens", metadata.get("rewind", 0)),
    )
    if isinstance(rewind_split_tokens, bool):
        rewind_split_tokens = int(rewind_split_tokens)
    try:
        rewind_split_tokens = int(rewind_split_tokens or 0)
    except (TypeError, ValueError):
        rewind_split_tokens = 0

    first_plan = model_plan[0]
    try:
        global_max_tokens = int(
            config.get(
                "global_max_tokens",
                metadata.get("global_max_tokens", first_plan["max_tokens"]),
            )
        )
    except (TypeError, ValueError):
        global_max_tokens = int(first_plan["max_tokens"])
    try:
        global_n_branches = int(
            config.get(
                "global_n_branches",
                metadata.get("global_n_branches", first_plan["n_branches"]),
            )
        )
    except (TypeError, ValueError):
        global_n_branches = int(first_plan["n_branches"])

    return {
        "name": metadata.get("name"),
        "show_model_names": bool(show_model_names),
        "rewind_split_tokens": max(0, rewind_split_tokens),
        "global_max_tokens": max(10, min(global_max_tokens, 8000)),
        "global_n_branches": max(1, min(global_n_branches, 64)),
        "model_plan": model_plan,
    }


def _metadata_without_tree_settings(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in _CONFIG_METADATA_KEYS
        and key not in {"config", "last_node_id", "name", "named_from"}
    }


@dataclass(frozen=True)
class Node:
    id: str
    parent_id: str | None
    text: str
    model: str | None
    strategy: str | None
    max_tokens: int | None
    temperature: float | None
    created_at: str
    metadata: dict[str, Any]
    tree_id: str
    kind: str = "text"
    context_id: str | None = None
    checked_out: bool = False


@dataclass(frozen=True)
class Tree:
    id: str
    current_node_id: str | None
    name: str | None
    show_model_names: bool
    rewind_split_tokens: int
    global_max_tokens: int
    global_n_branches: int
    model_plan: list[dict[str, Any]]
    created_at: str
    updated_at: str
    metadata: dict[str, Any]
    archived: bool


class AmbiguousNodeReference(ValueError):
    """Raised when a partial node reference matches more than one node."""

    def __init__(self, reference: str, matches: list[str]) -> None:
        self.reference = reference
        self.matches = matches
        super().__init__(
            f"ambiguous node reference {reference!r}: matches {', '.join(matches)}"
        )


class GenerationStore:
    """Persistent node store.

    A root node contains the user-provided starting text. Each generated
    continuation is a child node containing only the added segment. Full text is
    reconstructed by walking ancestors from the selected node back to its root.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.db_path = (
            Path(path).expanduser() if path is not None else default_db_path()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self.connect()) as conn, conn:
            _schema.initialize(
                conn,
                now=_now,
                normalize_root_metadata_config=_normalize_root_metadata_config,
                tree_settings_from_metadata=_tree_settings_from_metadata,
                metadata_without_tree_settings=_metadata_without_tree_settings,
            )

    def create_root(self, text: str, *, metadata: dict[str, Any] | None = None) -> Node:
        node_id = uuid.uuid4().hex
        raw_metadata = metadata or {}
        settings = _tree_settings_from_metadata(raw_metadata)
        config = (
            raw_metadata.get("config")
            if isinstance(raw_metadata.get("config"), dict)
            else {}
        )
        context = config.get("context", raw_metadata.get("context"))
        context_node: Node | None = None
        if isinstance(context, str) and context:
            context_node = Node(
                id=uuid.uuid4().hex,
                parent_id=None,
                text=context,
                model=None,
                strategy=None,
                max_tokens=None,
                temperature=None,
                created_at=_now(),
                metadata={},
                tree_id=node_id,
                kind="context",
                context_id=None,
                checked_out=False,
            )
        node = Node(
            id=node_id,
            parent_id=None,
            tree_id=node_id,
            kind="root",
            text=text,
            context_id=context_node.id if context_node else None,
            model=None,
            strategy=None,
            max_tokens=None,
            temperature=None,
            checked_out=False,
            created_at=_now(),
            metadata=_metadata_without_tree_settings(raw_metadata),
        )
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO trees (
                    id, current_node_id, name, show_model_names,
                    rewind_split_tokens, global_max_tokens, global_n_branches,
                    model_plan_json, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    node_id,
                    settings["name"],
                    int(settings["show_model_names"]),
                    settings["rewind_split_tokens"],
                    settings["global_max_tokens"],
                    settings["global_n_branches"],
                    json.dumps(settings["model_plan"], sort_keys=True),
                    node.created_at,
                    node.created_at,
                    json.dumps({}, sort_keys=True),
                ),
            )
            if context_node is not None:
                self._insert_with_conn(conn, context_node)
            self._insert_with_conn(conn, node)
        return node

    def create_context(
        self,
        tree_id: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Node:
        tree = self.get_tree(tree_id)
        if tree is None:
            raise KeyError(f"unknown tree: {tree_id}")
        node = Node(
            id=uuid.uuid4().hex,
            parent_id=None,
            tree_id=tree.id,
            kind="context",
            text=text,
            context_id=None,
            model=None,
            strategy=None,
            max_tokens=None,
            temperature=None,
            checked_out=False,
            created_at=_now(),
            metadata=metadata or {},
        )
        self._insert(node)
        return node

    def add_child(
        self,
        parent_id: str,
        text: str,
        *,
        model: str,
        strategy: str,
        max_tokens: int,
        temperature: float,
        metadata: dict[str, Any] | None = None,
    ) -> Node:
        parent = self.get(parent_id)
        if parent is None:
            raise KeyError(f"unknown parent node: {parent_id}")
        node = Node(
            id=uuid.uuid4().hex,
            parent_id=parent.id,
            tree_id=parent.tree_id,
            kind="text",
            text=text,
            context_id=parent.context_id,
            model=model,
            strategy=strategy,
            max_tokens=max_tokens,
            temperature=temperature,
            checked_out=False,
            created_at=_now(),
            metadata=metadata or {},
        )
        self._insert(node)
        return node

    def save_continuations(
        self,
        prefix: str,
        continuations: list[str],
        *,
        model: str,
        strategy: str,
        max_tokens: int,
        temperature: float,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Node, list[Node]]:
        """Persist one generation fanout and return its parent plus children."""
        parent = self.get(parent_id) if parent_id else self.create_root(prefix)
        if parent is None:
            raise KeyError(f"unknown parent node: {parent_id}")
        children = [
            self.add_child(
                parent.id,
                text,
                model=model,
                strategy=strategy,
                max_tokens=max_tokens,
                temperature=temperature,
                metadata=metadata,
            )
            for text in continuations
        ]
        return parent, children

    def get_tree(self, tree_id: str) -> Tree | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM trees WHERE id = ?", (tree_id,)
            ).fetchone()
        return self._tree(row) if row else None

    def tree_for_node(self, node_id: str) -> Tree:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        tree = self.get_tree(node.tree_id)
        if tree is None:
            raise KeyError(f"unknown tree: {node.tree_id}")
        return tree

    def update_tree_settings(
        self,
        tree_id: str,
        *,
        model_plan: list[dict[str, Any]] | None = None,
        show_model_names: bool | None = None,
        rewind_split_tokens: int | None = None,
        global_max_tokens: int | None = None,
        global_n_branches: int | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Tree:
        tree = self.get_tree(tree_id)
        if tree is None:
            raise KeyError(f"unknown tree: {tree_id}")
        merged_metadata = {**tree.metadata, **(metadata or {})}
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                UPDATE trees
                SET model_plan_json = ?,
                    show_model_names = ?,
                    rewind_split_tokens = ?,
                    global_max_tokens = ?,
                    global_n_branches = ?,
                    name = ?,
                    updated_at = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps(
                        model_plan if model_plan is not None else tree.model_plan,
                        sort_keys=True,
                    ),
                    int(
                        show_model_names
                        if show_model_names is not None
                        else tree.show_model_names
                    ),
                    int(
                        rewind_split_tokens
                        if rewind_split_tokens is not None
                        else tree.rewind_split_tokens
                    ),
                    global_max_tokens
                    if global_max_tokens is not None
                    else tree.global_max_tokens,
                    global_n_branches
                    if global_n_branches is not None
                    else tree.global_n_branches,
                    name if name is not None else tree.name,
                    _now(),
                    json.dumps(merged_metadata, sort_keys=True),
                    tree_id,
                ),
            )
        updated = self.get_tree(tree_id)
        assert updated is not None
        return updated

    def set_tree_archived(self, tree_id: str, archived: bool) -> Tree:
        tree = self.get_tree(tree_id)
        if tree is None:
            raise KeyError(f"unknown tree: {tree_id}")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE trees SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), _now(), tree_id),
            )
        updated = self.get_tree(tree_id)
        assert updated is not None
        return updated

    def resolve_node_id(self, reference: str) -> str | None:
        """Resolve a full id or unique id substring to a canonical node id."""
        if not reference:
            return None
        with closing(self.connect()) as conn:
            exact = conn.execute(
                "SELECT id FROM nodes WHERE id = ?",
                (reference,),
            ).fetchone()
            if exact:
                return str(exact["id"])

            rows = conn.execute(
                "SELECT id FROM nodes WHERE id LIKE ? ORDER BY created_at DESC, id DESC",
                (f"{reference}%",),
            ).fetchall()

        matches = [str(row["id"]) for row in rows]
        if not matches:
            return None
        if len(matches) > 1:
            raise AmbiguousNodeReference(reference, matches)
        return matches[0]

    def get(self, node_id: str) -> Node | None:
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return None
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (resolved,)
            ).fetchone()
        return self._node(row) if row else None

    def root(self, node_id: str) -> Node:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM nodes
                WHERE tree_id = ? AND parent_id IS NULL AND kind != 'context'
                ORDER BY created_at, id
                LIMIT 1
                """,
                (node.tree_id,),
            ).fetchone()
        root = self._node(row) if row else None
        if root is None:
            raise KeyError(f"unknown root node for tree: {node.tree_id}")
        return root

    def update_text(self, node_id: str, text: str) -> Node:
        """Rewrite a node's text in place.

        Used for roots, which have no sibling position to fork into: replacing
        one would mean starting a whole new tree and leaving every branch
        behind on the old one.
        """
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE nodes SET text = ? WHERE id = ?", (text, node_id))
        updated = self.get(node_id)
        assert updated is not None
        return updated

    def update_metadata(self, node_id: str, metadata: dict[str, Any]) -> Node:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        tree_updates: dict[str, Any] = {}
        if node.parent_id is None and "name" in metadata:
            tree_updates["name"] = metadata["name"]
            metadata = {k: v for k, v in metadata.items() if k != "name"}
        for key in ("current_node_id", "last_node_id"):
            if node.parent_id is None and key in metadata:
                tree_updates["current_node_id"] = metadata[key]
                metadata = {k: v for k, v in metadata.items() if k != key}
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT metadata_json FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown node: {node_id}")
            current = json.loads(str(row["metadata_json"]))
            merged = {**current, **metadata}
            conn.execute(
                "UPDATE nodes SET metadata_json = ? WHERE id = ?",
                (json.dumps(merged, sort_keys=True), node_id),
            )
            if tree_updates:
                if "current_node_id" in tree_updates:
                    conn.execute(
                        """
                        UPDATE trees
                        SET current_node_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (tree_updates["current_node_id"], _now(), node.tree_id),
                    )
                conn.execute(
                    "UPDATE trees SET name = COALESCE(?, name), updated_at = ? WHERE id = ?",
                    (tree_updates.get("name"), _now(), node.tree_id),
                )
        updated = self.get(node_id)
        assert updated is not None
        return updated

    def set_node_context(self, node_id: str, context_id: str | None) -> Node:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        if context_id is not None:
            context = self.get(context_id)
            if context is None:
                raise KeyError(f"unknown context node: {context_id}")
            if context.tree_id != node.tree_id or context.kind != "context":
                raise ValueError(f"node {context_id!r} is not a context in this tree")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE nodes SET context_id = ? WHERE id = ?",
                (context_id, node.id),
            )
        updated = self.get(node.id)
        assert updated is not None
        return updated

    def children(self, node_id: str) -> list[Node]:
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            raise KeyError(f"unknown node: {node_id}")
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM nodes
                WHERE parent_id = ? AND kind != 'context'
                ORDER BY created_at, id
                """,
                (resolved,),
            ).fetchall()
        return [self._node(row) for row in rows]

    def tree(self, node_id: str) -> list[Node]:
        """Return all nodes in a tree, ordered by creation time."""
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return []
        node = self.get(resolved)
        if node is None:
            return []
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE tree_id = ? ORDER BY created_at, id",
                (node.tree_id,),
            ).fetchall()
        return [self._node(row) for row in rows]

    def distinct_roles(self, node_id: str) -> set[str]:
        """Return the set of distinct non-null chat roles across a node's tree."""
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return set()
        node = self.get(resolved)
        if node is None:
            return set()
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT json_extract(metadata_json, '$.role') AS role
                FROM nodes WHERE tree_id = ?
                """,
                (node.tree_id,),
            ).fetchall()
        return {str(row["role"]) for row in rows if row["role"] is not None}

    def find_root_by_text(self, text: str) -> Node | None:
        """Return the root node whose text exactly matches, or None."""
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE parent_id IS NULL AND text = ?",
                (text,),
            ).fetchone()
        return self._node(row) if row else None

    def import_nodes(self, nodes: list[Node]) -> int:
        """Insert nodes in topological order, skipping existing ids. Returns count inserted."""
        # Topological sort: parents before children
        by_id = {n.id: n for n in nodes}
        ordered: list[Node] = []
        seen: set[str] = set()

        def visit(node: Node) -> None:
            if node.id in seen:
                return
            if node.parent_id and node.parent_id in by_id:
                visit(by_id[node.parent_id])
            seen.add(node.id)
            ordered.append(node)

        for n in nodes:
            visit(n)

        inserted = 0
        with closing(self.connect()) as conn, conn:
            for node in ordered:
                tree_id = node.tree_id or node.id
                if node.parent_id is None and node.kind != "context":
                    settings = _tree_settings_from_metadata(node.metadata)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO trees (
                            id, current_node_id, name, show_model_names,
                            rewind_split_tokens, global_max_tokens,
                            global_n_branches, model_plan_json, created_at,
                            updated_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tree_id,
                            node.id,
                            settings["name"],
                            int(settings["show_model_names"]),
                            settings["rewind_split_tokens"],
                            settings["global_max_tokens"],
                            settings["global_n_branches"],
                            json.dumps(settings["model_plan"], sort_keys=True),
                            node.created_at,
                            _now(),
                            json.dumps({}, sort_keys=True),
                        ),
                    )
                metadata = _metadata_without_tree_settings(node.metadata)
                if node.parent_id is not None or node.kind == "context":
                    metadata = node.metadata
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO nodes (
                        id, tree_id, parent_id, kind, text, context_id,
                        model, strategy, max_tokens, temperature, checked_out,
                        created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        tree_id,
                        node.parent_id,
                        node.kind,
                        node.text,
                        node.context_id,
                        node.model,
                        node.strategy,
                        node.max_tokens,
                        node.temperature,
                        int(node.checked_out),
                        node.created_at,
                        json.dumps(metadata, sort_keys=True),
                    ),
                )
                inserted += result.rowcount
        return inserted

    def tree_index(self) -> dict[str, tuple[str | None, str | None]]:
        """Return ``{tree_id: (name, current_node_id)}`` for every tree in one query."""
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT id, name, current_node_id FROM trees"
            ).fetchall()
        return {str(row["id"]): (row["name"], row["current_node_id"]) for row in rows}

    def tree_facets(self) -> dict[str, dict[str, list[str]]]:
        """Return ``{tree_id: {"sources": [...], "models": [...]}}`` in two queries.

        Used by the tree picker to show provenance (import source) and the
        distinct models that appear in each tree.
        """
        sources: dict[str, set[str]] = {}
        models: dict[str, set[str]] = {}
        with closing(self.connect()) as conn:
            for row in conn.execute(
                "SELECT DISTINCT tree_id, json_extract(metadata_json, '$.source') "
                "AS source FROM nodes "
                "WHERE json_extract(metadata_json, '$.source') IS NOT NULL"
            ):
                sources.setdefault(str(row["tree_id"]), set()).add(str(row["source"]))
            for row in conn.execute(
                "SELECT DISTINCT tree_id, model FROM nodes WHERE model IS NOT NULL"
            ):
                models.setdefault(str(row["tree_id"]), set()).add(str(row["model"]))
        out: dict[str, dict[str, list[str]]] = {}
        for tree_id in set(sources) | set(models):
            out[tree_id] = {
                "sources": sorted(sources.get(tree_id, set())),
                "models": sorted(models.get(tree_id, set())),
            }
        return out

    def tree_classifications(self) -> dict[str, dict[str, str]]:
        """Return ``{tree_id: {category, domain, sensitivity, source}}`` in one query.

        Values come from the tree row's ``metadata_json`` (the LLM classification
        written by the import/classify pipeline). Missing keys default to "".
        Trees without any of these keys are omitted.
        """
        keys = ("category", "domain", "sensitivity", "source")
        out: dict[str, dict[str, str]] = {}
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT id, metadata_json FROM trees").fetchall()
        for row in rows:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            if not isinstance(metadata, dict):
                continue
            values = {
                key: str(metadata[key])
                for key in keys
                if isinstance(metadata.get(key), str) and metadata[key]
            }
            if values:
                out[str(row["id"])] = {key: values.get(key, "") for key in keys}
        return out

    def node_tree_map(self, node_ids: list[str]) -> dict[str, str]:
        """Map node ids to their tree id in bulk. Unknown ids are omitted."""
        out: dict[str, str] = {}
        unique = [nid for nid in dict.fromkeys(node_ids) if nid]
        if not unique:
            return out
        with closing(self.connect()) as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT id, tree_id FROM nodes WHERE id IN ({placeholders})", chunk
                ).fetchall()
                for row in rows:
                    out[str(row["id"])] = str(row["tree_id"])
        return out

    def search_index_status(self) -> dict[str, bool]:
        """Report which retrieval indexes exist in this database.

        Returns ``{"fts": bool, "vec": bool}`` based on the presence of the
        ``nodes_fts`` (FTS5) and ``nodes_vec`` (sqlite-vec) tables that the
        guardian-angel corpus pipeline builds. loom's own databases have neither.
        """
        with closing(self.connect()) as conn:
            present = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('nodes_fts', 'nodes_vec')"
                ).fetchall()
            }
        return {"fts": "nodes_fts" in present, "vec": "nodes_vec" in present}

    def nodes_by_ids(self, node_ids: list[str]) -> dict[str, Node]:
        """Fetch multiple nodes by exact id in bulk. Unknown ids are omitted."""
        out: dict[str, Node] = {}
        unique = [nid for nid in dict.fromkeys(node_ids) if nid]
        if not unique:
            return out
        with closing(self.connect()) as conn:
            # Chunk to stay under SQLite's bound-parameter limit.
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({placeholders})", chunk
                ).fetchall()
                for row in rows:
                    out[str(row["id"])] = self._node(row)
        return out

    def roots(self, *, archived: bool | None = False) -> list[Node]:
        """List root nodes. `archived=False` (default) hides archived trees,

        `True` returns only archived trees, `None` returns both.
        """
        with closing(self.connect()) as conn:
            if archived is None:
                rows = conn.execute(
                    """
                    SELECT * FROM nodes
                    WHERE parent_id IS NULL AND kind != 'context'
                    ORDER BY created_at DESC, id DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT nodes.* FROM nodes
                    JOIN trees ON trees.id = nodes.tree_id
                    WHERE nodes.parent_id IS NULL AND nodes.kind != 'context'
                      AND trees.archived = ?
                    ORDER BY nodes.created_at DESC, nodes.id DESC
                    """,
                    (int(archived),),
                ).fetchall()
        return [self._node(row) for row in rows]

    def delete_tree(self, root_id: str) -> int:
        """Delete a root and every node in its tree. Returns deleted node count."""
        root = self.root(root_id)
        nodes = self.tree(root.id)
        node_ids = [node.id for node in nodes]
        if not node_ids:
            return 0

        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM nodes WHERE tree_id = ?", (root.tree_id,))
            conn.execute("DELETE FROM trees WHERE id = ?", (root.tree_id,))
        return len(node_ids)

    def delete_subtree(self, node_id: str) -> int:
        """Delete a node and all descendants. Returns deleted node count."""
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return 0
        node = self.get(resolved)
        if node is None:
            return 0
        if node.parent_id is None:
            return self.delete_tree(node.id)

        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM nodes WHERE id = ?
                    UNION ALL
                    SELECT n.id FROM nodes n JOIN subtree s ON n.parent_id = s.id
                )
                SELECT id FROM subtree
                """,
                (node.id,),
            ).fetchall()
        node_ids = [str(row[0]) for row in rows]
        if not node_ids:
            return 0

        with closing(self.connect()) as conn, conn:
            result = conn.execute("DELETE FROM nodes WHERE id = ?", (node.id,))
        return result.rowcount + len(node_ids) - 1

    def subtree_edges(self, node_id: str) -> list[tuple[str, str | None]]:
        """Return (id, parent_id) for `node_id` and every descendant.

        `node_id` itself is included with its real parent_id (which callers
        computing shape metrics should treat as the subtree's root, i.e.
        ignore any edge pointing outside the returned set).
        """
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return []
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE sub(id, parent_id) AS (
                    SELECT id, parent_id FROM nodes WHERE id = ? AND kind != 'context'
                    UNION ALL
                    SELECT n.id, n.parent_id FROM nodes n
                    JOIN sub s ON n.parent_id = s.id
                    WHERE n.kind != 'context'
                )
                SELECT id, parent_id FROM sub
                """,
                (resolved,),
            ).fetchall()
        return [(str(row["id"]), row["parent_id"]) for row in rows]

    def descendant_count(self, node_id: str) -> int:
        """Return the total number of descendants (not including the node itself)."""
        resolved = self.resolve_node_id(node_id)
        if resolved is None:
            return 0
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                WITH RECURSIVE desc(id) AS (
                    SELECT id FROM nodes WHERE parent_id = ?
                    UNION ALL
                    SELECT n.id FROM nodes n JOIN desc d ON n.parent_id = d.id
                )
                SELECT COUNT(*) FROM desc
                """,
                (resolved,),
            ).fetchone()
        return int(row[0]) if row else 0

    def descendant_counts(self, node_ids: list[str]) -> dict[str, int]:
        """Return descendant counts for multiple nodes in a single query."""
        if not node_ids:
            return {}
        placeholders = ",".join("?" * len(node_ids))
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                WITH RECURSIVE desc(id, parent_root_id) AS (
                    SELECT id, parent_id FROM nodes WHERE parent_id IN ({placeholders})
                    UNION ALL
                    SELECT n.id, d.parent_root_id FROM nodes n JOIN desc d ON n.parent_id = d.id
                )
                SELECT parent_root_id, COUNT(*) FROM desc GROUP BY parent_root_id
                """,
                node_ids,
            ).fetchall()
        counts = {nid: 0 for nid in node_ids}
        for row in rows:
            if row[0] in counts:
                counts[row[0]] = int(row[1])
        return counts

    def flagged_nodes(
        self, *, model: str | None = None, limit: int = 100
    ) -> list[Node]:
        """Nodes with a recorded generation-quality issue, newest first.

        An explicit flag and an in-place boundary correction are the same
        quality signal. Query both so historical corrections made before they
        automatically set ``flagged`` remain visible too. A corpus large
        enough for that scan to hurt wants partial indexes on these JSON
        expressions; nothing here depends on the storage staying JSON.
        """
        sql = [
            "SELECT * FROM nodes",
            "WHERE (json_extract(metadata_json, '$.flagged') = 1",
            "OR COALESCE(json_array_length(json_extract(metadata_json, '$.in_place_edits')), 0) > 0)",
        ]
        params: list[Any] = []
        if model:
            sql.append("AND model = ?")
            params.append(model)
        sql.append("ORDER BY created_at DESC, id DESC LIMIT ?")
        params.append(limit)
        with closing(self.connect()) as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()
        return [self._node(row) for row in rows]

    def flag_counts_by_model(self) -> dict[str, dict[str, int]]:
        """Per model: how many generated nodes there are, and how many had issues.

        Both halves matter — three flags against a model used three times is a
        different signal from three against a model used three hundred times.
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    model,
                    COUNT(*) AS generated,
                    SUM(
                        CASE WHEN json_extract(metadata_json, '$.flagged') = 1
                            OR COALESCE(
                                json_array_length(
                                    json_extract(metadata_json, '$.in_place_edits')
                                ),
                                0
                            ) > 0
                        THEN 1 ELSE 0 END
                    ) AS flagged
                FROM nodes
                WHERE model IS NOT NULL
                GROUP BY model
                ORDER BY model
                """
            ).fetchall()
        return {
            str(row["model"]): {
                "generated": int(row["generated"]),
                "flagged": int(row["flagged"] or 0),
            }
            for row in rows
        }

    def speed_stats_by_model(self) -> dict[str, dict[str, float | int]]:
        """Per model: observed generation speed, averaged over timed nodes.

        Only nodes that carry `metadata.timing` (set for streamed
        completions with a first-token timestamp) count; a model that has
        only been reached through import or a non-streaming path has no
        speed record here even if it has generated nodes.
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    model,
                    COUNT(*) AS timed_nodes,
                    AVG(json_extract(metadata_json, '$.timing.ttft_ms')) AS avg_ttft_ms,
                    AVG(json_extract(metadata_json, '$.timing.elapsed_ms')) AS avg_elapsed_ms,
                    AVG(json_extract(metadata_json, '$.timing.completion_tokens_per_second'))
                        AS avg_completion_tokens_per_second,
                    SUM(json_extract(metadata_json, '$.timing.completion_tokens'))
                        AS total_completion_tokens
                FROM nodes
                WHERE model IS NOT NULL
                    AND json_extract(metadata_json, '$.timing.elapsed_ms') IS NOT NULL
                GROUP BY model
                ORDER BY model
                """
            ).fetchall()
        return {
            str(row["model"]): {
                "timed_nodes": int(row["timed_nodes"]),
                "avg_ttft_ms": round(row["avg_ttft_ms"], 3),
                "avg_elapsed_ms": round(row["avg_elapsed_ms"], 3),
                "avg_completion_tokens_per_second": (
                    round(row["avg_completion_tokens_per_second"], 3)
                    if row["avg_completion_tokens_per_second"] is not None
                    else None
                ),
                "total_completion_tokens": int(row["total_completion_tokens"] or 0),
            }
            for row in rows
        }

    def corpus_evidence_rows(
        self, *, window_start: str | None = None, window_end: str | None = None
    ) -> list[dict[str, Any]]:
        """Return privacy-safe corpus aggregates for the shared evidence store."""
        filters = ["n.model IS NOT NULL", "n.kind = 'text'"]
        params: list[Any] = []
        if window_start is not None:
            filters.append("n.created_at >= ?")
            params.append(window_start)
        if window_end is not None:
            filters.append("n.created_at < ?")
            params.append(window_end)
        where = " AND ".join(filters)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                WITH RECURSIVE depths(id, depth) AS (
                    SELECT id, 0 FROM nodes WHERE parent_id IS NULL
                    UNION ALL
                    SELECT n.id, depths.depth + 1
                    FROM nodes n JOIN depths ON n.parent_id = depths.id
                ), evidence AS (
                    SELECT n.model, n.strategy AS prompt_method,
                        CASE
                            WHEN depths.depth <= 4 THEN '0-4'
                            WHEN depths.depth <= 14 THEN '5-14'
                            WHEN depths.depth <= 29 THEN '15-29'
                            ELSE '30+'
                        END AS depth_bucket,
                        CASE
                            WHEN COALESCE(json_array_length(json_extract(n.metadata_json, '$.in_place_edits')), 0) > 0
                                THEN 'boundary_edit'
                            WHEN json_extract(n.metadata_json, '$.flagged') = 1
                                THEN 'manual_flag'
                            ELSE 'none'
                        END AS issue_kind,
                        CASE WHEN json_extract(n.metadata_json, '$.flagged') = 1
                            OR COALESCE(json_array_length(json_extract(n.metadata_json, '$.in_place_edits')), 0) > 0
                            THEN 1 ELSE 0 END AS flagged,
                        CASE WHEN COALESCE(json_array_length(json_extract(n.metadata_json, '$.in_place_edits')), 0) > 0
                            THEN 1 ELSE 0 END AS corrected,
                        CASE WHEN json_extract(n.metadata_json, '$.flagged') = 1
                            AND COALESCE(json_array_length(json_extract(n.metadata_json, '$.in_place_edits')), 0) = 0
                            THEN 1 ELSE 0 END AS open_issue,
                        json_extract(n.metadata_json, '$.timing.ttft_ms') AS ttft_ms,
                        json_extract(n.metadata_json, '$.timing.elapsed_ms') AS elapsed_ms,
                        json_extract(n.metadata_json, '$.timing.completion_tokens_per_second') AS tokens_per_second,
                        json_extract(n.metadata_json, '$.timing.completion_tokens') AS completion_tokens
                    FROM nodes n JOIN depths ON depths.id = n.id
                    WHERE {where}
                )
                SELECT model, prompt_method, depth_bucket, issue_kind,
                    COUNT(*) AS generated_count,
                    COUNT(*) AS successful_count,
                    SUM(flagged) AS flagged_count,
                    SUM(corrected) AS corrected_count,
                    SUM(open_issue) AS open_issue_count,
                    COUNT(elapsed_ms) AS timed_count,
                    AVG(ttft_ms) AS avg_ttft_ms,
                    AVG(elapsed_ms) AS avg_elapsed_ms,
                    AVG(tokens_per_second) AS avg_completion_tokens_per_second,
                    SUM(completion_tokens) AS total_completion_tokens
                FROM evidence
                GROUP BY model, prompt_method, depth_bucket, issue_kind
                ORDER BY model, prompt_method, depth_bucket, issue_kind
                """,
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            timing = {
                "avg_ttft_ms": round(row["avg_ttft_ms"], 3)
                if row["avg_ttft_ms"] is not None
                else None,
                "avg_elapsed_ms": round(row["avg_elapsed_ms"], 3)
                if row["avg_elapsed_ms"] is not None
                else None,
                "avg_completion_tokens_per_second": round(
                    row["avg_completion_tokens_per_second"], 3
                )
                if row["avg_completion_tokens_per_second"] is not None
                else None,
                "total_completion_tokens": int(row["total_completion_tokens"] or 0),
            }
            result.append(
                {
                    "model": str(row["model"]),
                    "prompt_method": (
                        str(row["prompt_method"])
                        if row["prompt_method"] is not None
                        else None
                    ),
                    "depth_bucket": str(row["depth_bucket"]),
                    "issue_kind": str(row["issue_kind"]),
                    "generated_count": int(row["generated_count"]),
                    "successful_count": int(row["successful_count"]),
                    "flagged_count": int(row["flagged_count"] or 0),
                    "corrected_count": int(row["corrected_count"] or 0),
                    "open_issue_count": int(row["open_issue_count"] or 0),
                    "timed_count": int(row["timed_count"]),
                    "timing_summary": timing,
                }
            )
        return result

    def recent(self, limit: int = 20) -> list[Node]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM nodes ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._node(row) for row in rows]

    def move_children(self, from_parent_id: str, to_parent_id: str) -> int:
        """Reparent every child of ``from_parent_id`` onto ``to_parent_id``.

        Used when a node is replaced by a rewritten sibling (see
        :meth:`LoomSession.apply_edit`): the continuations hanging off the old
        text have to follow the edit, or the edited branch becomes a dead end.
        Returns the number of children moved.
        """
        source = self.get(from_parent_id)
        target = self.get(to_parent_id)
        if source is None:
            raise KeyError(f"unknown node: {from_parent_id}")
        if target is None:
            raise KeyError(f"unknown node: {to_parent_id}")
        if source.id == target.id:
            return 0
        if any(node.id == target.id for node in self.lineage(source.id)):
            raise ValueError(f"{to_parent_id!r} is an ancestor of {from_parent_id!r}")

        moved = [child.id for child in self.children(source.id)]
        if not moved:
            return 0
        # A checked-out child coming across wins the destination's pointer: it
        # is the path the caller was standing on.
        incoming_checked_out = self.get_checked_out_child_id(source.id)
        with closing(self.connect()) as conn, conn:
            conn.executemany(
                "UPDATE nodes SET parent_id = ? WHERE id = ?",
                [(target.id, child_id) for child_id in moved],
            )
            if incoming_checked_out is not None:
                conn.execute(
                    "UPDATE nodes SET checked_out = 0 WHERE parent_id = ? AND id != ?",
                    (target.id, incoming_checked_out),
                )
        return len(moved)

    def set_checked_out_child(self, parent_id: str, child_id: str) -> None:
        parent = self.get(parent_id)
        child = self.get(child_id)
        if parent is None:
            raise KeyError(f"unknown parent node: {parent_id}")
        if child is None:
            raise KeyError(f"unknown child node: {child_id}")
        if child.parent_id != parent.id:
            raise ValueError(f"node {child_id!r} is not a child of {parent_id!r}")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE nodes SET checked_out = 0 WHERE parent_id = ?",
                (parent.id,),
            )
            conn.execute(
                "UPDATE nodes SET checked_out = 1 WHERE id = ?",
                (child.id,),
            )

    def get_checked_out_child_id(self, parent_id: str) -> str | None:
        resolved = self.resolve_node_id(parent_id)
        if resolved is None:
            return None
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT id FROM nodes
                WHERE parent_id = ? AND checked_out = 1
                ORDER BY created_at, id
                LIMIT 1
                """,
                (resolved,),
            ).fetchone()
        if row is not None:
            return str(row["id"])
        return None

    def set_active_node(self, node_id: str) -> None:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"unknown node: {node_id}")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE trees SET current_node_id = ?, updated_at = ? WHERE id = ?",
                (node.id, _now(), node.tree_id),
            )

    def get_active_node_id(self) -> str | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT current_node_id FROM trees
                WHERE current_node_id IS NOT NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else str(row["current_node_id"])

    def get_active_node(self) -> Node | None:
        active_node_id = self.get_active_node_id()
        return None if active_node_id is None else self.get(active_node_id)

    def select_branch(self, node_id: str, branch_index: int) -> Node:
        children = self.children(node_id)
        if branch_index < 1:
            raise ValueError("branch index must be >= 1")
        if branch_index > len(children):
            raise IndexError(
                f"node {node_id!r} has only {len(children)} child branch(es)"
            )
        return children[branch_index - 1]

    def lineage(self, node_id: str) -> list[Node]:
        nodes: list[Node] = []
        node = self.get(node_id)
        while node is not None:
            nodes.append(node)
            node = self.get(node.parent_id) if node.parent_id else None
        nodes.reverse()
        if not nodes:
            raise KeyError(f"unknown node: {node_id}")
        return nodes

    def full_text(self, node_id: str) -> str:
        return "".join(
            node.text for node in self.lineage(node_id) if node.kind != "context"
        )

    def _insert(self, node: Node) -> None:
        with closing(self.connect()) as conn, conn:
            self._insert_with_conn(conn, node)

    def _insert_with_conn(self, conn: sqlite3.Connection, node: Node) -> None:
        conn.execute(
            """
            INSERT INTO nodes (
                id, tree_id, parent_id, kind, text, context_id, model, strategy,
                max_tokens, temperature, checked_out, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id,
                node.tree_id,
                node.parent_id,
                node.kind,
                node.text,
                node.context_id,
                node.model,
                node.strategy,
                node.max_tokens,
                node.temperature,
                int(node.checked_out),
                node.created_at,
                json.dumps(node.metadata, sort_keys=True),
            ),
        )

    @staticmethod
    def _node(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"],
            parent_id=row["parent_id"],
            tree_id=row["tree_id"],
            kind=row["kind"],
            text=row["text"],
            context_id=row["context_id"],
            model=row["model"],
            strategy=row["strategy"],
            max_tokens=row["max_tokens"],
            temperature=row["temperature"],
            checked_out=bool(row["checked_out"]),
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _tree(row: sqlite3.Row) -> Tree:
        raw_plan = json.loads(row["model_plan_json"])
        model_plan = normalize_model_plan(raw_plan)
        if not model_plan:
            model_plan = _DEFAULT_MODEL_PLAN
        return Tree(
            id=row["id"],
            current_node_id=row["current_node_id"],
            name=row["name"],
            show_model_names=bool(row["show_model_names"]),
            rewind_split_tokens=int(row["rewind_split_tokens"] or 0),
            global_max_tokens=int(row["global_max_tokens"] or 200),
            global_n_branches=int(row["global_n_branches"] or 1),
            model_plan=model_plan,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]),
            archived=bool(row["archived"]),
        )
