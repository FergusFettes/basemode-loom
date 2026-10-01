"""FTS5 keyword index over node text, for the tree picker's keyword search.

Loom's search reads a ``nodes_fts(node_id UNINDEXED, text)`` table when one
exists (see `search.KeywordBackend`). Corpus databases built elsewhere ship
with it; this builds the same table for any loom or chat database. It is an
explicit, opt-in projection like the vector index: nothing on loom's write
path maintains it, so new nodes are searchable after the next
``--incremental`` run.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

FTS_TABLE = "nodes_fts"
_EXPECTED_COLUMNS = ["node_id", "text"]


def fts_columns(conn: sqlite3.Connection) -> list[str] | None:
    """Columns of an existing keyword index, or None when there is none."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type = 'table'",
        (FTS_TABLE,),
    ).fetchone()
    if exists is None:
        return None
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({FTS_TABLE})")]


def build_fts_index(
    db_path: Path, *, min_chars: int = 1, incremental: bool = False
) -> int:
    """Build or top up the keyword index; returns how many nodes were added.

    A full build drops and recreates the table. ``incremental`` keeps it,
    indexes nodes it has not seen, and prunes deleted ones; text edited in
    place is only picked up by a full build. A ``nodes_fts`` with some other
    shape (a corpus built by another tool) is refused rather than replaced.
    """
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    if min_chars < 0:
        raise ValueError("min_chars must be non-negative")

    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA busy_timeout = 30000")
        columns = fts_columns(conn)
        if columns is not None and columns != _EXPECTED_COLUMNS:
            raise ValueError(
                f"{FTS_TABLE} already exists with columns {columns}; "
                "refusing to replace an index this tool did not build"
            )
        if columns is not None and incremental:
            conn.execute(
                f"DELETE FROM {FTS_TABLE} WHERE node_id NOT IN (SELECT id FROM nodes)"
            )
            missing = f"AND id NOT IN (SELECT node_id FROM {FTS_TABLE})"
        else:
            conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
            conn.execute(
                f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(node_id UNINDEXED, text)"
            )
            missing = ""
        cursor = conn.execute(
            f"INSERT INTO {FTS_TABLE} (node_id, text) "
            f"SELECT id, text FROM nodes WHERE length(text) >= ? {missing}",
            (min_chars,),
        )
        return cursor.rowcount
