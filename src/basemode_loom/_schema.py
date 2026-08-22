"""SQLite schema creation and in-place migrations for generation stores."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from typing import Any

LATEST_USER_VERSION = 4


def initialize(
    conn: sqlite3.Connection,
    *,
    now: Callable[[], str],
    normalize_root_metadata_config: Callable[[dict[str, Any]], dict[str, Any]],
    tree_settings_from_metadata: Callable[[dict[str, Any]], dict[str, Any]],
    metadata_without_tree_settings: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    """Create the current schema and migrate an existing database in place."""
    had_nodes = table_exists(conn, "nodes")
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
            metadata_json TEXT NOT NULL DEFAULT '{}',
            archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    ensure_column(conn, "trees", "archived", "INTEGER NOT NULL DEFAULT 0")
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
        ensure_column(conn, "nodes", "tree_id", "TEXT")
        ensure_column(conn, "nodes", "kind", "TEXT NOT NULL DEFAULT 'text'")
        ensure_column(conn, "nodes", "context_id", "TEXT")
        ensure_column(conn, "nodes", "checked_out", "INTEGER NOT NULL DEFAULT 0")

    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version < 2:
        migrate_to_v2(conn, normalize_root_metadata_config)
    if version < 3:
        migrate_to_v3(
            conn,
            now=now,
            tree_settings_from_metadata=tree_settings_from_metadata,
            metadata_without_tree_settings=metadata_without_tree_settings,
        )
    if version < 4:
        migrate_to_v4(conn)
    create_indexes(conn)
    if version < LATEST_USER_VERSION:
        conn.execute(f"PRAGMA user_version = {LATEST_USER_VERSION}")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def has_column(conn: sqlite3.Connection, table: str, name: str) -> bool:
    return name in {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def ensure_column(
    conn: sqlite3.Connection, table: str, name: str, definition: str
) -> None:
    if not has_column(conn, table, name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def create_indexes(conn: sqlite3.Connection) -> None:
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


def migrate_to_v2(
    conn: sqlite3.Connection,
    normalize_root_metadata_config: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    """Make root metadata config canonical and remove duplicate config keys."""
    rows = conn.execute(
        "SELECT id, metadata_json FROM nodes WHERE parent_id IS NULL"
    ).fetchall()
    for row in rows:
        metadata = json.loads(str(row["metadata_json"]))
        if not isinstance(metadata, dict):
            metadata = {}
        normalized = normalize_root_metadata_config(metadata)
        if normalized != metadata:
            conn.execute(
                "UPDATE nodes SET metadata_json = ? WHERE id = ?",
                (json.dumps(normalized, sort_keys=True), row["id"]),
            )


def migrate_to_v3(
    conn: sqlite3.Connection,
    *,
    now: Callable[[], str],
    tree_settings_from_metadata: Callable[[dict[str, Any]], dict[str, Any]],
    metadata_without_tree_settings: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    """Create first-class tree rows and move tree settings out of root metadata."""
    roots = conn.execute(
        "SELECT * FROM nodes WHERE parent_id IS NULL ORDER BY created_at, id"
    ).fetchall()
    for root in roots:
        root_id = str(root["id"])
        metadata = json.loads(str(root["metadata_json"]))
        if not isinstance(metadata, dict):
            metadata = {}
        settings = tree_settings_from_metadata(metadata)
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
                now(),
                json.dumps(tree_metadata, sort_keys=True),
            ),
        )
        if has_column(conn, "nodes", "root_id"):
            conn.execute(
                "UPDATE nodes SET tree_id = ? WHERE root_id = ?", (root_id, root_id)
            )
        else:
            conn.execute(
                "UPDATE nodes SET tree_id = ? WHERE tree_id = ? OR id = ?",
                (root_id, root_id, root_id),
            )
        config = (
            metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
        )
        context = config.get("context", metadata.get("context"))
        if isinstance(context, str) and context:
            context_id = uuid.uuid4().hex
            if has_column(conn, "nodes", "root_id"):
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
                "UPDATE nodes SET context_id = ? WHERE id = ?", (context_id, root_id)
            )
        conn.execute(
            "UPDATE nodes SET metadata_json = ? WHERE id = ?",
            (
                json.dumps(metadata_without_tree_settings(metadata), sort_keys=True),
                root_id,
            ),
        )

    checked_rows = []
    if table_exists(conn, "state"):
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
                "UPDATE nodes SET checked_out = 0 WHERE parent_id = ?", (parent_id,)
            )
            conn.execute("UPDATE nodes SET checked_out = 1 WHERE id = ?", (child_id,))


def migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Drop legacy root_id/branch_index/state storage after tree migration."""
    if not has_column(conn, "nodes", "root_id") and not has_column(
        conn, "nodes", "branch_index"
    ):
        if table_exists(conn, "state"):
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
        SELECT id, COALESCE(tree_id, root_id, id), parent_id, kind, text,
            context_id, model, strategy, max_tokens, temperature, checked_out,
            created_at, metadata_json
        FROM nodes
        """
    )
    conn.execute("DROP TABLE nodes")
    conn.execute("ALTER TABLE nodes_new RENAME TO nodes")
    if table_exists(conn, "state"):
        conn.execute("DROP TABLE state")
