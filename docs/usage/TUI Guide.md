# TUI Guide

Launch the interactive TUI with:

```bash
basemode-loom view
```

Or open a specific tree:

```bash
basemode-loom view <node-id>
```

## Layout

The TUI has two main view modes:

**Branch view** (default): Shows the current path from root to the active node, then the selected child's continuation below. Sibling branches are listed on the right.

**Tree view**: Shows the full tree structure with all nodes and their relationship. Toggle with `v`.

## Keybindings

### Navigation

| Key | Action |
|-----|--------|
| `l` or `→` | Move to selected child |
| `h` or `←` | Move to parent |
| `j` or `↓` | Select next sibling |
| `k` or `↑` | Select previous sibling |

### Generation

| Key | Action |
|-----|--------|
| `Space` | Generate N continuations from current node |
| `m` | Open model picker |
| `N` | Set number of branches |
| `T` | Set max tokens |

Generation streams tokens in real time. Press `Esc` to cancel mid-generation.

### Editing

| Key | Action |
|-----|--------|
| `e` | Edit current node's text in `$EDITOR` |

When you save and close the editor, the node text is updated and the tree recalculates from that node.

### View

| Key | Action |
|-----|--------|
| `v` | Toggle branch view / tree view |
| `H` | Toggle showing model names on nodes |
| `z` | Hoist: make current node the display root |
| `Z` | Unhoist |

### Bookmarks

| Key | Action |
|-----|--------|
| `b` | Toggle bookmark on current node |
| `B` | Jump to next bookmarked node |

### Other

| Key | Action |
|-----|--------|
| `?` | Show stats screen for current tree |
| `o` | Open tree picker (switch to a different tree) |
| `q` | Quit |

## Tree picker

Press `o` to open the tree picker. Use `j`/`k` to navigate the list of recent trees and `Enter` to switch to one.

## Stats screen

Press `?` to open the stats overlay for the current tree. This shows:

- Total nodes and tree depth
- Expansion rate per node
- Model breakdown (which models generated which nodes)
- Descendant scores (a heuristic for which branches were found most useful, based on how much they were expanded)
