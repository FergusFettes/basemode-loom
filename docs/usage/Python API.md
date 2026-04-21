# Python API

The public API is importable from `basemode_loom`:

```python
from basemode_loom import GenerationStore, LoomSession, Node, default_db_path
```

## GenerationStore

The main persistence interface. Wraps a SQLite database.

```python
from basemode_loom import GenerationStore

# Default path: ~/.local/share/basemode-loom/loom.db
store = GenerationStore()

# Custom path
store = GenerationStore("/path/to/custom.db")
```

### Creating nodes

```python
# Create a root node
root = store.create_root("Once upon a time")

# Add a single child
child = store.add_child(
    parent_id=root.id,
    text=" there lived a wizard",
    model="gpt-4o-mini",
    strategy="system",
    max_tokens=200,
    temperature=0.9,
)

# Save multiple continuations at once (the typical case)
parent, children = store.save_continuations(
    prefix="Once upon a time",
    continuations=[" there lived a wizard", " in a land far away", " a clock stopped"],
    model="gpt-4o-mini",
    strategy="system",
    max_tokens=200,
    temperature=0.9,
    parent_id=root.id,  # optional; creates root if omitted
)
```

`save_continuations` returns a tuple of `(parent_node, list_of_child_nodes)`. The `parent_node` is the node the continuations were added to — either the existing `parent_id` node or a newly created root.

### Querying

```python
# Get a single node by ID (supports prefix matching)
node = store.get("abc123")

# Get the root of any node's tree
root = store.root(node.id)

# Get all children
children = store.children(node.id)

# Get full tree as flat list
all_nodes = store.tree(root.id)

# Get all root nodes
roots = store.roots()

# Get recently created nodes
recent = store.recent(limit=20)

# Get lineage from node back to root (root first)
lineage = store.lineage(node.id)

# Get full reconstructed text
text = store.full_text(node.id)
```

### State tracking

```python
# Set/get the active node (cursor position)
store.set_active_node(node.id)
active = store.get_active_node()

# Track which child is "checked out" at each parent
store.set_checked_out_child(parent_id=parent.id, child_id=child.id)
child_id = store.get_checked_out_child_id(parent.id)
```

### Metadata

```python
# Update metadata on a node (merges with existing)
updated = store.update_metadata(node.id, {"bookmarked": True, "rating": 4})
```

### Tree operations

```python
# Delete an entire tree (returns number of nodes deleted)
count = store.delete_tree(root.id)

# Import a list of nodes (e.g. from export)
imported = store.import_nodes(nodes)

# Descendant counts (useful for stats)
count = store.descendant_count(node.id)
counts = store.descendant_counts([node.id for node in nodes])

# Resolve a node ID from a short prefix or other reference
resolved_id = store.resolve_node_id("abc1")
```

## LoomSession

Stateful session manager for interactive use. Handles navigation, generation, and bookmarking on top of a store.

```python
from basemode_loom import GenerationStore, LoomSession

store = GenerationStore()
root = store.roots()[0]
session = LoomSession(store, start_id=root.id)
```

### Reading state

```python
state = session.get_state()

# SessionState fields
state.current_node       # Node
state.full_text          # str — reconstructed from lineage
state.children           # list[Node]
state.selected_child_idx # int
state.model              # str
state.max_tokens         # int
state.n_branches         # int
state.view_mode          # "branch" | "tree"
```

### Navigation

```python
state = session.navigate_child()    # move to selected child
state = session.navigate_parent()   # move to parent
state = session.select_sibling(+1)  # select next sibling (doesn't navigate)
state = session.select_sibling(-1)  # select previous sibling
```

### Generation

```python
import asyncio

async def generate():
    async for event in session.generate():
        match event:
            case TokenReceived(branch_idx=i, token=t):
                print(f"Branch {i}: {t}", end="", flush=True)
            case GenerationComplete(new_nodes=nodes):
                print(f"\nGenerated {len(nodes)} nodes")
            case GenerationError(error=e):
                print(f"Error: {e}")
            case GenerationCancelled():
                print("Cancelled")

asyncio.run(generate())

# Cancel mid-generation
session.cancel()
```

### Configuration

```python
session.set_model("claude-3-5-haiku-20241022")
session.set_max_tokens(400)
session.set_n_branches(5)
```

### Bookmarks

```python
bookmarked = session.toggle_bookmark()  # True if now bookmarked
state = session.next_bookmark()         # jump to next bookmarked node
```

### Editing

```python
# Apply an edit: provide original and edited text
# Returns the updated Node or None if no change
updated_node = session.apply_edit(original_text, edited_text)
```

### View modes

```python
state = session.toggle_tree_view()    # branch ↔ tree
state = session.toggle_model_names() # show/hide model labels
state = session.toggle_hoist()       # hoist current node as display root
```

### Persistence

```python
session.save()  # persist active node and checked-out children to store
```

## Node

Nodes are frozen dataclasses — immutable after creation.

```python
from basemode_loom import Node

node.id            # str — UUID hex
node.parent_id     # str | None
node.root_id       # str
node.text          # str — only this segment
node.model         # str | None
node.strategy      # str | None
node.max_tokens    # int | None
node.temperature   # float | None
node.branch_index  # int | None — 0-based; None if not branched
node.created_at    # str — ISO 8601 with Z suffix
node.metadata      # dict[str, Any] — extensible
```

## Default database path

```python
from basemode_loom import default_db_path

path = default_db_path()  # Path object
```
