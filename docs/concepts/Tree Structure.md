# Tree Structure

## Nodes

Every piece of text in a loom is a `Node`. Nodes have:

- A unique `id` (UUID hex)
- A `parent_id` (or `None` for root nodes)
- A `root_id` pointing to the root of the tree
- A `text` segment — only the text added at this node, not the full text
- Generation metadata: `model`, `strategy`, `max_tokens`, `temperature`
- A `branch_index` (0-based) when generated alongside siblings, `None` otherwise
- A `metadata` dict for extensible fields like `name`, `bookmarked`, `rating`

## Root nodes

A root node is a node with no parent. Its `text` is the initial prompt you provided. Every tree has exactly one root.

## Full text

The full text at any node is the concatenation of all `text` segments from root to that node:

```
root.text + child.text + grandchild.text + ...
```

Call `store.full_text(node_id)` to reconstruct it.

## Branching

When you generate N continuations at once, each becomes a child with `branch_index` 0 through N-1. Single continuations (N=1) get `branch_index=None`.

```
root: "The wizard"
  ├── [0] " opened the ancient tome"
  ├── [1] " raised his staff"
  └── [2] " looked at the stars"
```

## Navigation state

The store tracks two pieces of ephemeral state:

- **Active node**: the cursor position — which node you're currently viewing
- **Checked-out child**: per parent, which child is currently selected

This means when you navigate back to a parent and then forward again, you return to the same child you were on before — just like a git checkout.

## Example tree

```
root: "The last human sat alone"          (root_id=abc, parent=None)
  ├── " in a ruined city"                 (branch_index=0)
  │     └── ", surrounded by silence"     (branch_index=None)
  ├── " in a vast library"                (branch_index=1)
  │     ├── " reading the final book"     (branch_index=0)
  │     └── " cataloguing everything"     (branch_index=1)
  └── " watching the last sunset"         (branch_index=2)
```

Full text of the innermost left node:
```
"The last human sat alone in a ruined city, surrounded by silence"
```
