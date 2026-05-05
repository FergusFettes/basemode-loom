# Quickstart

## 1. Generate your first tree

```bash
basemode-loom run "Once upon a time there was a" -n 3 -m gpt-4o-mini -M 200
```

This creates a root node with your prompt, then generates 3 continuations. By default the tree is saved to `~/.local/share/basemode/generations.sqlite` unless `BASEMODE_DB` or `--db` overrides it.

## 2. Open the TUI

```bash
basemode-loom view
```

You'll see the interactive tree explorer. The current node's text is shown in context, with sibling branches listed below.

### Basic navigation

| Key | Action |
|-----|--------|
| `l` or `→` | Go to selected child |
| `h` or `←` | Go to parent |
| `j` / `k` | Select next/previous sibling |
| `Space` | Generate more continuations from here |
| `Tab` | Open the tree picker |
| `q` | Quit |

## 3. Keep exploring

Navigate to an interesting branch and press `Space` to generate from there. Each generation creates new child nodes.

Press `v` to toggle between branch view (focused on the current path) and tree view (full tree structure).
Press `m` to pick one or more models for the next generation batch. Branch count is per enabled model.

## 4. From the CLI

```bash
# See recent nodes
basemode-loom nodes

# Show the active node's full text
basemode-loom active

# Generate from the current active node
basemode-loom continue -n 3

# Export the tree to markdown
basemode-loom export --to file.md
```

The default command is `view`, so `basemode-loom <node-id-or-text>` also opens the TUI unless the input resolves to another subcommand.

## 5. Python API

```python
from basemode_loom import GenerationStore

store = GenerationStore()
root = store.create_root("Once upon a time")
print(store.roots())
```

See [[Python API]] for the full API reference.
