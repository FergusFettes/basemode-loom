"""sqlite-vec helpers for semantic search over corpus databases."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

VEC_TABLE = "nodes_vec"
META_TABLE = "vec_meta"


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _import_sqlite_vec():
    try:
        import sqlite_vec
    except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "semantic search needs sqlite-vec; install basemode-loom[embed]"
        ) from exc
    return sqlite_vec


def load_vec(conn: sqlite3.Connection):
    sqlite_vec = _import_sqlite_vec()
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return sqlite_vec


def vec_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (VEC_TABLE,),
    ).fetchone()
    return row is not None


def read_meta(conn: sqlite3.Connection) -> tuple[str, int] | None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type = 'table'",
        (META_TABLE,),
    ).fetchone()
    if row is None:
        return None
    meta = conn.execute(f"SELECT model, dim FROM {META_TABLE} WHERE id = 1").fetchone()
    return (str(meta[0]), int(meta[1])) if meta else None


def embed_corpus(
    db_path: Path,
    embedder: Embedder,
    *,
    min_chars: int = 1,
    batch_size: int = 64,
    incremental: bool = False,
) -> int:
    """Build or update the in-database vector projection over node text."""
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    if min_chars < 0:
        raise ValueError("min_chars must be non-negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    dim = embedder.dim
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA busy_timeout = 30000")
        sqlite_vec = load_vec(conn)
        reuse = (
            incremental and vec_exists(conn) and read_meta(conn) == (embedder.name, dim)
        )
        if reuse:
            existing = {
                str(row[0]) for row in conn.execute(f"SELECT node_id FROM {VEC_TABLE}")
            }
            current = {str(row[0]) for row in conn.execute("SELECT id FROM nodes")}
            stale = existing - current
            if stale:
                conn.executemany(
                    f"DELETE FROM {VEC_TABLE} WHERE node_id = ?",
                    [(node_id,) for node_id in stale],
                )
        else:
            conn.execute(f"DROP TABLE IF EXISTS {VEC_TABLE}")
            conn.execute(
                f"CREATE VIRTUAL TABLE {VEC_TABLE} "
                f"USING vec0(node_id TEXT PRIMARY KEY, embedding float[{dim}])"
            )
            existing = set()

        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "model TEXT NOT NULL, dim INTEGER NOT NULL)"
        )
        indexed = 0
        ids: list[str] = []
        texts: list[str] = []

        def flush() -> None:
            nonlocal indexed
            if not texts:
                return
            vectors = embedder.embed(texts)
            conn.executemany(
                f"INSERT INTO {VEC_TABLE} (node_id, embedding) VALUES (?, ?)",
                [
                    (node_id, sqlite_vec.serialize_float32(vector))
                    for node_id, vector in zip(ids, vectors, strict=True)
                ],
            )
            indexed += len(texts)
            ids.clear()
            texts.clear()

        for node_id, text in conn.execute("SELECT id, text FROM nodes"):
            value = text or ""
            if len(value) < min_chars or str(node_id) in existing:
                continue
            ids.append(str(node_id))
            texts.append(str(value))
            if len(texts) >= batch_size:
                flush()
        flush()

        conn.execute(f"DELETE FROM {META_TABLE}")
        conn.execute(
            f"INSERT INTO {META_TABLE} (id, model, dim) VALUES (1, ?, ?)",
            (embedder.name, dim),
        )
        return indexed


def vector_search(
    conn: sqlite3.Connection, query_vector: list[float], limit: int
) -> list[str]:
    sqlite_vec = _import_sqlite_vec()
    rows = conn.execute(
        f"""
        SELECT node_id
        FROM {VEC_TABLE}
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
        """,
        (sqlite_vec.serialize_float32(query_vector), limit),
    ).fetchall()
    return [str(row[0]) for row in rows]


def vector_count(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        if not vec_exists(conn):
            return 0
        load_vec(conn)
        row = conn.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}").fetchone()
        return int(row[0]) if row else 0
