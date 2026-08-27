"""Reproducible /api/trees benchmark for synthetic loom corpora.

Run with, for example::

    uv run python benchmarks/benchmark_tree_catalog.py --trees 100 --nodes 10000

The topology alternates between shallow/wide stars and deep/narrow chains.
Each scenario reports median endpoint wall time and the number of SELECT/WITH
statements observed through SQLite's trace callback.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.store import GenerationStore


def _select_counter(statements: list[int]):
    def trace(sql: str) -> None:
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(1)

    return trace


def _traced_connect(original_connect, statements: list[int]):
    def connect() -> sqlite3.Connection:
        conn = original_connect()
        conn.set_trace_callback(_select_counter(statements))
        return conn

    return connect


def build_corpus(path: Path, tree_count: int, node_count: int) -> GenerationStore:
    """Build a deterministic mixed-topology corpus with an FTS5 index."""
    store = GenerationStore(path)
    per_tree, remainder = divmod(node_count, tree_count)
    trees: list[tuple[object, ...]] = []
    nodes: list[tuple[object, ...]] = []
    fts: list[tuple[str, str]] = []
    epoch = datetime(2020, 1, 1, tzinfo=UTC)
    for tree_index in range(tree_count):
        size = per_tree + (tree_index < remainder)
        tree_id = f"t{tree_index:07d}"
        stamp = (epoch + timedelta(seconds=tree_index)).isoformat()
        updated = (epoch + timedelta(seconds=tree_count - tree_index)).isoformat()
        archived = int(tree_index % 5 == 0)
        metadata = json.dumps(
            {
                "category": "code" if tree_index % 2 else "research",
                "domain": "agents" if tree_index % 3 else "chemistry",
                "source": "synthetic",
            }
        )
        current_id = f"{tree_id}n{size - 1:04d}"
        trees.append(
            (
                tree_id, current_id, f"Synthetic {tree_index}", 1, 0, 200, 1,
                "[]", stamp, updated, metadata, archived,
            )
        )
        for node_index in range(size):
            node_id = f"{tree_id}n{node_index:04d}"
            if node_index == 0:
                parent_id = None
                kind = "root"
            else:
                parent_id = (
                    f"{tree_id}n0000"
                    if tree_index % 2 == 0
                    else f"{tree_id}n{node_index - 1:04d}"
                )
                kind = "text"
            text = (
                "unique catalogue needle" if tree_index == 1 and node_index == 1
                else f"synthetic text {tree_index} {node_index}"
            )
            nodes.append(
                (
                    node_id, tree_id, parent_id, kind, text, None,
                    "openai/gpt-5", "synthetic", 16, 0.0, 0, stamp,
                    json.dumps({"source": "synthetic"}),
                )
            )
            fts.append((node_id, text))
    with closing(store.connect()) as conn, conn:
        conn.execute("DELETE FROM nodes")
        conn.execute("DELETE FROM trees")
        conn.executemany(
            "INSERT INTO trees VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", trees
        )
        conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            nodes,
        )
        conn.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(node_id UNINDEXED, text)")
        conn.executemany("INSERT INTO nodes_fts(node_id, text) VALUES (?, ?)", fts)
        conn.execute("ANALYZE")
    return store


def measure(store: GenerationStore, path: str, repeats: int) -> dict[str, object]:
    timings: list[float] = []
    counts: list[int] = []
    original_connect = store.connect
    for _ in range(repeats):
        traced: list[int] = []

        store.connect = _traced_connect(  # type: ignore[method-assign]
            original_connect, traced
        )
        started = perf_counter()
        with TestClient(create_app(store)) as client:
            response = client.get(path)
        timings.append((perf_counter() - started) * 1000)
        counts.append(len(traced))
        store.connect = original_connect  # type: ignore[method-assign]
        if response.status_code != 200:
            return {"unsupported": response.status_code, "detail": response.text}
    return {
        "median_ms": round(statistics.median(timings), 2),
        "sql_queries": int(statistics.median(counts)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trees", type=int, required=True)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory() if args.db is None else None
    db_path = args.db or Path(temporary.name) / "catalog.sqlite"
    store = build_corpus(db_path, args.trees, args.nodes)
    parameters = {
        parameter["name"]
        for parameter in create_app(store).openapi()["paths"]["/api/trees"]["get"][
            "parameters"
        ]
    }
    scenarios = {
        "first_active_page": "/api/trees?limit=50",
        "recent": "/api/trees?sort=recent&limit=50",
        "nodes": "/api/trees?sort=nodes&limit=50",
        "archived": "/api/trees?archived=archived&limit=50",
        "both": "/api/trees?archived=both&limit=50",
        "keyword": "/api/trees?q=needle&limit=50",
    }
    print(
        json.dumps(
            {
                "trees": args.trees,
                "nodes": args.nodes,
                "results": {
                    name: (
                        {"unsupported": "archived selector not published"}
                        if name in {"archived", "both"} and "archived" not in parameters
                        else measure(store, path, args.repeats)
                    )
                    for name, path in scenarios.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
