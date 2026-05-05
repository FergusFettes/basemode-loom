# basemode-loom

**basemode-loom** is a persistent branching exploration tool for LLM text generation. It lets you generate multiple continuations from any point in a text, navigate the resulting tree, and build up complex branching narratives or explorations — all saved to a local SQLite database.

## What it does

When you're exploring what an LLM might write, you often want to see several possibilities at once and follow the most interesting one further. basemode-loom makes that workflow first-class:

- Generate **N parallel continuations** from any node in a tree
- **Navigate** the tree interactively — parent, child, siblings
- **Bookmark** nodes, edit text, switch models mid-tree
- **Persist** everything locally; resume any tree at any time
- Analyze tree shape and generation quality with built-in **stats**

## Interfaces

| Interface | Use case |
|-----------|----------|
| [[CLI Reference]] | Scripting, quick generation, import/export, inspection |
| [[TUI Guide]] | Interactive exploration (`basemode-loom view`) |
| [[Configuration]] | Keybindings, generation defaults, per-model overrides |
| [[Python API]] | Embedding in your own tools |
| [[Web Server]] | Headless server + REST/WebSocket API |

## Quick example

```bash
# Generate 3 continuations from a prompt
basemode-loom run "The last human on Earth sat alone in a room" -n 3 -m gpt-4o-mini

# Open interactive TUI to explore the tree
basemode-loom view
```

See [[Quickstart]] to get running in 5 minutes.
