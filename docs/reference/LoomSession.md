# LoomSession

`basemode_loom.session.LoomSession`

Stateful session manager. The single shared interface between all UI layers — TUI, web backend, and CLI all talk to the store through `LoomSession`. UI layers receive `SessionState` snapshots and never call the store directly.

## Constructor

```python
LoomSession(store: GenerationStore, start_id: str)
```

`start_id` can be any node ID in a tree; the session opens at that node.

## SessionState

`get_state()` returns a frozen `SessionState` snapshot:

| Field | Type | Description |
|-------|------|-------------|
| `current_node_id` | `str` | Active node ID |
| `current_node` | `Node` | Active node object |
| `full_text` | `str` | Reconstructed text from root to current node |
| `children` | `list[Node]` | Direct children of current node |
| `selected_child_idx` | `int` | Index into `children` |
| `descendant_counts` | `dict[str, int]` | Descendant count per child ID |
| `continuation_text` | `str` | Full text of the selected child's path |
| `model` | `str` | Current model |
| `max_tokens` | `int` | Current max tokens |
| `temperature` | `float` | Current temperature |
| `n_branches` | `int` | Number of branches to generate |
| `context` | `str` | System prompt / context |
| `root_id` | `str` | Root node ID |
| `view_mode` | `"branch" \| "tree"` | Current display mode |
| `hoisted_node_id` | `str \| None` | Node used as display root (hoist mode) |
| `tree_nodes` | `list[Node] \| None` | All nodes; populated in tree view mode |
| `show_model_names` | `bool` | Whether model names are shown in the UI |

## Navigation

All navigation methods return an updated `SessionState`.

```python
session.navigate_child()       # move to selected child
session.navigate_parent()      # move to parent
session.select_sibling(+1)     # select next sibling
session.select_sibling(-1)     # select previous sibling
```

`select_sibling` wraps around at the ends.

## Generation

```python
async for event in session.generate():
    ...
```

`generate()` is an async generator that yields `GenerationEvent` values:

| Event | Fields | Description |
|-------|--------|-------------|
| `TokenReceived` | `branch_idx: int`, `token: str` | Streamed token during generation |
| `GenerationComplete` | `completions: list[str]`, `new_nodes: list[Node]` | All branches done |
| `GenerationError` | `error: Exception` | Generation failed |
| `GenerationCancelled` | — | Cancelled via `session.cancel()` |

```python
session.cancel()  # signal cancellation; will yield GenerationCancelled
```

## Configuration

```python
session.set_model("claude-3-5-haiku-20241022")
session.set_max_tokens(400)
session.set_n_branches(5)
```

Changes apply to the next call to `generate()` but do not alter existing nodes.

## Bookmarks

```python
bookmarked = session.toggle_bookmark()  # bool — True if now bookmarked
state = session.next_bookmark()         # navigate to next bookmarked node
```

## Editing

```python
updated = session.apply_edit(original_text, edited_text)
# Returns updated Node, or None if no change detected
```

The edit is applied to the current node's text segment. If the original text is found in the lineage reconstruction, the matching node's text is updated.

## View toggles

```python
state = session.toggle_tree_view()    # "branch" ↔ "tree"
state = session.toggle_model_names()
state = session.toggle_hoist()        # hoist current node as root / unhoist
```

## Persistence

```python
session.save()
```

Persists the active node and all checked-out child selections to the store.

## Properties

```python
session.store  # GenerationStore
```
