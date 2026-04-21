# Node

`basemode_loom.store.Node`

A frozen dataclass representing a single node in a generation tree. Nodes are immutable after creation; all fields are set at insert time except `metadata`, which can be updated via `store.update_metadata()`.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | UUID hex, unique across all trees |
| `parent_id` | `str \| None` | Parent node ID; `None` for root nodes |
| `root_id` | `str` | ID of the root node of this tree |
| `text` | `str` | Only this node's text segment — not the full text |
| `model` | `str \| None` | Model that generated this node |
| `strategy` | `str \| None` | Generation strategy (from `basemode`) |
| `max_tokens` | `int \| None` | Max tokens used for generation |
| `temperature` | `float \| None` | Temperature used for generation |
| `branch_index` | `int \| None` | 0-based index among siblings; `None` for non-branched |
| `created_at` | `str` | ISO 8601 timestamp with Z suffix |
| `metadata` | `dict[str, Any]` | Extensible metadata dict |

## Metadata keys

The `metadata` dict is open-ended. Conventional keys used by the application:

| Key | Type | Set by |
|-----|------|--------|
| `name` | `str` | Auto-naming; also user-settable |
| `bookmarked` | `bool` | TUI bookmark toggle |
| `model` | `str` | Root nodes: default model for the tree |
| `max_tokens` | `int` | Root nodes: default max tokens |
| `temperature` | `float` | Root nodes: default temperature |
| `n_branches` | `int` | Root nodes: default number of branches |
| `context` | `str` | Root nodes: system prompt / context |

## Notes

- Root nodes have `parent_id = None` and `root_id = id` (self-referential)
- `text` on a root node is the initial prompt; on child nodes it's the generated continuation segment
- Full text is obtained via `store.full_text(node.id)`, which walks lineage and concatenates `text` fields
