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

_LATEST_USER_VERSION = 4
_CONFIG_METADATA_KEYS = {
    "context",
    "max_tokens",
    "model",
    "model_plan",
    "n_branches",
    "rewind",
    "rewind_split_tokens",
    "show_model_names",
    "temperature",
}

_DEFAULT_MODEL_PLAN = [
    {
        "model": "gpt-4o-mini",
        "n_branches": 1,
        "max_tokens": 200,
        "temperature": 0.9,
        "enabled": True,
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

    model_plan = _normalize_model_plan(config.get("model_plan"))
    if not model_plan:
        model_plan = _model_plan_from_legacy(metadata, config)

    new_config: dict[str, Any] = {}
    context = config.get("context", metadata.get("context"))
    if isinstance(context, str):
        new_config["context"] = context

    show_model_names = config.get("show_model_names", metadata.get("show_model_names"))
    if isinstance(show_model_names, bool):
        new_config["show_model_names"] = show_model_names

    if model_plan:
        new_config["model_plan"] = model_plan

    if new_config:
        normalized["config"] = new_config
    return normalized


def _normalize_model_plan(raw_plan: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_plan, list):
        return []
    plan: list[dict[str, Any]] = []
    for entry in raw_plan:
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model", "")).strip()
        if not model:
            continue
        plan.append(
            {
                "model": model,
                "n_branches": max(1, int(entry.get("n_branches", 1))),
                "max_tokens": max(50, min(int(entry.get("max_tokens", 200)), 8000)),
                "temperature": float(entry.get("temperature", 0.9)),
                "enabled": bool(entry.get("enabled", True)),
            }
        )
    return plan


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
    model_plan = _normalize_model_plan(config.get("model_plan"))
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

    return {
        "name": metadata.get("name"),
        "show_model_names": bool(show_model_names),
        "rewind_split_tokens": max(0, rewind_split_tokens),
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
    model_plan: list[dict[str, Any]]
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


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
            had_nodes = self._table_exists(conn, "nodes")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trees (
                    id TEXT PRIMARY KEY,
                    current_node_id TEXT,
                    name TEXT,
                    show_model_names INTEGER NOT NULL DEFAULT 1,
                    rewind_split_tokens INTEGER NOT NULL DEFAULT 0,
                    model_plan_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    tree_id TEXT NOT NULL,
                    parent_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'text',
                    text TEXT NOT NULL,
                    context_id TEXT REFERENCES nodes(id),
                    model TEXT,
                    strategy TEXT,
                    max_tokens INTEGER,
                    temperature REAL,
                    checked_out INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            if had_nodes:
                self._ensure_column(conn, "nodes", "tree_id", "TEXT")
                self._ensure_column(
                    conn, "nodes", "kind", "TEXT NOT NULL DEFAULT 'text'"
                )
                self._ensure_column(conn, "nodes", "context_id", "TEXT")
                self._ensure_column(
                    conn, "nodes", "checked_out", "INTEGER NOT NULL DEFAULT 0"
                )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version < 2:
                self._migrate_to_v2(conn)
            if version < 3:
                self._migrate_to_v3(conn)
            if version < 4:
                self._migrate_to_v4(conn)
            self._create_indexes(conn)
            if version < _LATEST_USER_VERSION:
                conn.execute(f"PRAGMA user_version = {_LATEST_USER_VERSION}")

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )

    def _has_column(self, conn: sqlite3.Connection, table: str, name: str) -> bool:
        return name in {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_parent_created ON nodes(parent_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_tree_created ON nodes(tree_id, created_at)"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_one_checked_out_child
            ON nodes(parent_id)
            WHERE checked_out = 1 AND parent_id IS NOT NULL
            """
        )

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, name: str, definition: str
    ) -> None:
        if not self._has_column(conn, table, name):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        """Make root metadata config canonical and remove duplicate config keys."""
        rows = conn.execute(
            "SELECT id, metadata_json FROM nodes WHERE parent_id IS NULL"
        ).fetchall()
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            if not isinstance(metadata, dict):
                metadata = {}
            normalized = _normalize_root_metadata_config(metadata)
            if normalized != metadata:
                conn.execute(
                    "UPDATE nodes SET metadata_json = ? WHERE id = ?",
                    (json.dumps(normalized, sort_keys=True), row["id"]),
                )

    def _migrate_to_v3(self, conn: sqlite3.Connection) -> None:
        """Create first-class tree rows and move tree settings out of root metadata."""
        roots = conn.execute(
            "SELECT * FROM nodes WHERE parent_id IS NULL ORDER BY created_at, id"
        ).fetchall()
        for root in roots:
            root_id = str(root["id"])
            metadata = json.loads(str(root["metadata_json"]))
            if not isinstance(metadata, dict):
                metadata = {}
            settings = _tree_settings_from_metadata(metadata)
            tree_metadata = {
                key: metadata[key] for key in ("named_from",) if key in metadata
            }
            current_node_id = metadata.get("last_node_id")
            if (
                not isinstance(current_node_id, str)
                or conn.execute(
                    "SELECT 1 FROM nodes WHERE id = ?", (current_node_id,)
                ).fetchone()
                is None
            ):
                current_node_id = root_id

            conn.execute(
                """
                INSERT OR IGNORE INTO trees (
                    id, current_node_id, name, show_model_names,
                    rewind_split_tokens, model_plan_json, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root_id,
                    current_node_id,
                    settings["name"],
                    int(settings["show_model_names"]),
                    settings["rewind_split_tokens"],
                    json.dumps(settings["model_plan"], sort_keys=True),
                    root["created_at"],
                    _now(),
                    json.dumps(tree_metadata, sort_keys=True),
                ),
            )
            if self._has_column(conn, "nodes", "root_id"):
                conn.execute(
                    "UPDATE nodes SET tree_id = ? WHERE root_id = ?",
                    (root_id, root_id),
                )
            else:
                conn.execute(
                    "UPDATE nodes SET tree_id = ? WHERE tree_id = ? OR id = ?",
                    (root_id, root_id, root_id),
                )
            config = (
                metadata.get("config")
                if isinstance(metadata.get("config"), dict)
                else {}
            )
            context = config.get("context", metadata.get("context"))
            if isinstance(context, str) and context:
                context_id = uuid.uuid4().hex
                if self._has_column(conn, "nodes", "root_id"):
                    conn.execute(
                        """
                        INSERT INTO nodes (
                            id, tree_id, parent_id, root_id, kind, text, context_id,
                            model, strategy, max_tokens, temperature, branch_index,
                            checked_out, created_at, metadata_json
                        ) VALUES (?, ?, NULL, ?, 'context', ?, NULL, NULL, NULL,
                            NULL, NULL, NULL, 0, ?, '{}')
                        """,
                        (context_id, root_id, root_id, context, root["created_at"]),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO nodes (
                            id, tree_id, parent_id, kind, text, context_id, model,
                            strategy, max_tokens, temperature, checked_out,
                            created_at, metadata_json
                        ) VALUES (?, ?, NULL, 'context', ?, NULL, NULL, NULL,
                            NULL, NULL, 0, ?, '{}')
                        """,
                        (context_id, root_id, context, root["created_at"]),
                    )
                conn.execute(
                    "UPDATE nodes SET context_id = ? WHERE id = ?",
                    (context_id, root_id),
                )
            normalized_metadata = _metadata_without_tree_settings(metadata)
            conn.execute(
                "UPDATE nodes SET metadata_json = ? WHERE id = ?",
                (json.dumps(normalized_metadata, sort_keys=True), root_id),
            )

        checked_rows = []
        if self._table_exists(conn, "state"):
            checked_rows = conn.execute(
                "SELECT key, value FROM state WHERE key LIKE 'checked_out:%'"
            ).fetchall()
        for row in checked_rows:
            parent_id = str(row["key"]).split("checked_out:", 1)[-1]
            child_id = str(row["value"])
            child = conn.execute(
                "SELECT parent_id FROM nodes WHERE id = ?", (child_id,)
            ).fetchone()
            if child is not None and child["parent_id"] == parent_id:
                conn.execute(
                    "UPDATE nodes SET checked_out = 0 WHERE parent_id = ?",
                    (parent_id,),
                )
                conn.execute(
                    "UPDATE nodes SET checked_out = 1 WHERE id = ?",
                    (child_id,),
                )

    def _migrate_to_v4(self, conn: sqlite3.Connection) -> None:
        """Drop legacy root_id/branch_index/state storage after tree migration."""
        if not self._has_column(conn, "nodes", "root_id") and not self._has_column(
            conn, "nodes", "branch_index"
        ):
            if self._table_exists(conn, "state"):
                conn.execute("DROP TABLE state")
            return

        conn.execute("DROP INDEX IF EXISTS idx_nodes_root_created")
        conn.execute("DROP INDEX IF EXISTS idx_nodes_parent_created")
        conn.execute("DROP INDEX IF EXISTS idx_nodes_tree_created")
        conn.execute("DROP INDEX IF EXISTS idx_nodes_one_checked_out_child")
        conn.execute(
            """
            CREATE TABLE nodes_new (
                id TEXT PRIMARY KEY,
                tree_id TEXT NOT NULL,
                parent_id TEXT REFERENCES nodes_new(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'text',
                text TEXT NOT NULL,
                context_id TEXT REFERENCES nodes_new(id),
                model TEXT,
                strategy TEXT,
                max_tokens INTEGER,
                temperature REAL,
                checked_out INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO nodes_new (
                id, tree_id, parent_id, kind, text, context_id, model, strategy,
                max_tokens, temperature, checked_out, created_at, metadata_json
            )
            SELECT
                id,
                COALESCE(tree_id, root_id, id),
                parent_id,
                kind,
                text,
                context_id,
                model,
                strategy,
                max_tokens,
                temperature,
                checked_out,
                created_at,
                metadata_json
            FROM nodes
            """
        )
        conn.execute("DROP TABLE nodes")
        conn.execute("ALTER TABLE nodes_new RENAME TO nodes")
        if self._table_exists(conn, "state"):
            conn.execute("DROP TABLE state")

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
                    rewind_split_tokens, model_plan_json, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    node_id,
                    settings["name"],
                    int(settings["show_model_names"]),
                    settings["rewind_split_tokens"],
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
                    name if name is not None else tree.name,
                    _now(),
                    json.dumps(merged_metadata, sort_keys=True),
                    tree_id,
                ),
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
                            rewind_split_tokens, model_plan_json, created_at,
                            updated_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tree_id,
                            node.id,
                            settings["name"],
                            int(settings["show_model_names"]),
                            settings["rewind_split_tokens"],
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

    def roots(self) -> list[Node]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM nodes
                WHERE parent_id IS NULL AND kind != 'context'
                ORDER BY created_at DESC, id DESC
                """
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

    def recent(self, limit: int = 20) -> list[Node]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM nodes ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._node(row) for row in rows]

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
        model_plan = _normalize_model_plan(raw_plan)
        if not model_plan:
            model_plan = _DEFAULT_MODEL_PLAN
        return Tree(
            id=row["id"],
            current_node_id=row["current_node_id"],
            name=row["name"],
            show_model_names=bool(row["show_model_names"]),
            rewind_split_tokens=int(row["rewind_split_tokens"] or 0),
            model_plan=model_plan,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]),
        )
