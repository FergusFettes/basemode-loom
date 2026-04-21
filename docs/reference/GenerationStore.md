# GenerationStore

`basemode_loom.store.GenerationStore`

SQLite-backed persistence layer for generation trees. All reads and writes go through this class.

## Constructor

```python
GenerationStore(path: str | Path | None = None)
```

If `path` is `None`, uses `default_db_path()` (`~/.local/share/basemode-loom/loom.db`). The database file and parent directories are created if they don't exist.

## Writing

| Method | Returns | Description |
|--------|---------|-------------|
| `create_root(text, *, metadata=None)` | `Node` | Create a new root node |
| `add_child(parent_id, text, *, model, strategy, max_tokens, temperature, branch_index=None, metadata=None)` | `Node` | Add a single child node |
| `save_continuations(prefix, continuations, *, model, strategy, max_tokens, temperature, parent_id=None, metadata=None)` | `tuple[Node, list[Node]]` | Save N continuations; creates root if `parent_id` is None |
| `update_metadata(node_id, metadata)` | `Node` | Merge new metadata into existing |

## Reading

| Method | Returns | Description |
|--------|---------|-------------|
| `get(node_id)` | `Node \| None` | Fetch a single node by ID |
| `root(node_id)` | `Node` | Get the root of any node's tree |
| `children(node_id)` | `list[Node]` | Direct children, ordered by `branch_index` |
| `tree(root_id)` | `list[Node]` | All nodes in a tree, breadth-first |
| `roots()` | `list[Node]` | All root nodes, most recent first |
| `recent(limit=20)` | `list[Node]` | Recently created nodes |
| `lineage(node_id)` | `list[Node]` | Ancestors from root to node (inclusive) |
| `full_text(node_id)` | `str` | Reconstructed full text by concatenating lineage |

## State tracking

| Method | Returns | Description |
|--------|---------|-------------|
| `set_active_node(node_id)` | `None` | Set the cursor position |
| `get_active_node()` | `Node \| None` | Get the current cursor node |
| `set_checked_out_child(parent_id, child_id)` | `None` | Record which child is selected at a parent |
| `get_checked_out_child_id(parent_id)` | `str \| None` | Retrieve the checked-out child for a parent |

## Tree operations

| Method | Returns | Description |
|--------|---------|-------------|
| `delete_tree(root_id)` | `int` | Delete all nodes in a tree; returns count deleted |
| `import_nodes(nodes)` | `int` | Insert a list of `Node` objects; returns count inserted |

## Traversal utilities

| Method | Returns | Description |
|--------|---------|-------------|
| `descendant_count(node_id)` | `int` | Count all descendants of a node |
| `descendant_counts(node_ids)` | `dict[str, int]` | Batch version of `descendant_count` |
| `resolve_node_id(reference)` | `str \| None` | Resolve a short prefix, alias, or full ID to a node ID |

## Database schema

Two tables:

**`nodes`** — one row per node:
- `id TEXT PRIMARY KEY`
- `parent_id TEXT`
- `root_id TEXT`
- `text TEXT`
- `model TEXT`
- `strategy TEXT`
- `max_tokens INTEGER`
- `temperature REAL`
- `branch_index INTEGER`
- `created_at TEXT`
- `metadata TEXT` (JSON)

**`state`** — key/value table for ephemeral session state:
- `key TEXT PRIMARY KEY`
- `value TEXT`

State keys: `active_node_id`, `checked_out:{parent_id}`.
