"""sqlite-vec helpers for semantic search over corpus databases."""

from __future__ import annotations

import sqlite3

VEC_TABLE = "nodes_vec"
META_TABLE = "vec_meta"


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


def read_meta(conn: sqlite3.Connection) -> tuple[str, int] | None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type = 'table'",
        (META_TABLE,),
    ).fetchone()
    if row is None:
        return None
    meta = conn.execute(f"SELECT model, dim FROM {META_TABLE} WHERE id = 1").fetchone()
    return (str(meta[0]), int(meta[1])) if meta else None


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
