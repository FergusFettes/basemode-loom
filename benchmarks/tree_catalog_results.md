# Tree catalogue benchmark

Measured 2026-08-27 on the development Mac using Python 3.11.13, SQLite's WAL
mode, FastAPI `TestClient`, and three warm endpoint calls per scenario. Times
are medians in milliseconds. SQL counts include `SELECT` and `WITH` statements
observed across all store connections. The corpus generator alternates
shallow/wide stars and deep/narrow chains and creates an FTS5 index.

The baseline is commit `e74549c` (the pre-change 0.7.2 development tree, whose
catalogue implementation matches the inspected 0.7.1 path). The after result
includes the richer card metrics returned by this change.

## 100 trees / 10,000 nodes

| Scenario | Before ms / queries | After ms / queries |
|---|---:|---:|
| First active page, no search | 40.14 / 8 | 62.48 / 3 |
| Recent sort | 38.90 / 8 | 61.21 / 3 |
| Node-count sort | 39.37 / 8 | 64.63 / 3 |
| Archived only | unsupported | 28.87 / 3 |
| Active and archived | unsupported | 65.61 / 3 |
| FTS keyword (`needle`) | 39.93 / 11 | 34.67 / 6 |

## 1,000 trees / 100,000 nodes

| Scenario | Before ms / queries | After ms / queries |
|---|---:|---:|
| First active page, no search | 359.90 / 9 | 275.19 / 3 |
| Recent sort | 357.55 / 9 | 272.92 / 3 |
| Node-count sort | 358.87 / 9 | 301.86 / 3 |
| Archived only | unsupported | 97.21 / 3 |
| Active and archived | unsupported | 325.15 / 3 |
| FTS keyword (`needle`) | 358.44 / 12 | 241.31 / 6 |

The 10,000-node non-search cases trade some fixed SQLite shape-aggregation
cost for the new fields, so they regress in wall time despite dropping from
eight queries to three. At 100,000 nodes the paginated path is 16–33% faster
for the measured supported scenarios while returning all Grove card metrics.
Query count is independent of catalogue or page size. FTS adds three constant
queries for status, ranking, and node-to-tree mapping in this selective query.

## Query-plan notes

`EXPLAIN QUERY PLAN` was checked for the active/recent page and node-count
paths. The former searches `trees` through
`idx_trees_archived_updated (archived, updated_at DESC, id DESC)` and looks up
roots through the partial `idx_nodes_tree_root` index (current nodes use their
primary keys). Created ordering has the analogous
`idx_trees_archived_created` index. Node and model aggregation uses the
covering `idx_nodes_tree_model`; recursive child walks use
`idx_nodes_parent_created`. Temporary B-trees remain necessary for grouped
widths and topology sorts (`breadth` and `branching`), because those values are
derived rather than persisted.

Reproduce either size with:

```bash
uv run python benchmarks/benchmark_tree_catalog.py --trees 100 --nodes 10000
uv run python benchmarks/benchmark_tree_catalog.py --trees 1000 --nodes 100000
```
