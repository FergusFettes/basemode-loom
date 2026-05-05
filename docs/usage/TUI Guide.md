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

The subtitle/status bar also shows the active node ID, active model, branches per model, total enabled branches, tree token totals, estimated tree cost, and whether model names are shown.

## Keybindings

### Navigation

| Key | Action |
|-----|--------|
| `l` or `→` | Move to selected child |
| `h` or `←` | Move to parent |
| `j` or `↓` | Select next sibling |
| `k` or `↑` | Select previous sibling |
| `H` | Move the word cursor left inside the selected child |
| `L` | Move the word cursor right / clear the word cursor |

### Generation

| Key | Action |
|-----|--------|
| `Space` | Generate from the current node |
| `Shift+Space` | Quick-generate with `+10` max tokens |
| `1`-`9` | Set branches per model directly |
| `m` | Open the model picker |
| `w` / `s` | Increase / decrease max tokens by `50` |
| `t` | Enter max tokens explicitly |
| `d` / `a` | Increase / decrease branches per model |

Generation streams tokens in real time. Press `Esc` once to hide the stream and a second time to cancel the running generation.
The model picker supports multi-select, so one generation batch can fan out across several models. `n_branches` is the total enabled branches across the current model plan.

If a word cursor is active in branch view, `Space` truncates the selected child at that cursor position, creates a sibling fork, and generates from the truncated text.

### Editing

| Key | Action |
|-----|--------|
| `e` | Inline-edit the selected child segment |
| `E` | Edit the current node segment in `$EDITOR` |
| `c` | Edit the persisted context / system prompt for the tree |

Inline edits create a forked node rather than mutating an existing node in place. Full-text edits create a new edited lineage from the first changed segment onward.

### View

| Key | Action |
|-----|--------|
| `v` | Toggle branch view / tree view |
| `n` | Toggle showing model names on nodes |
| `Z` | Toggle hoist on the current node |

### Bookmarks

| Key | Action |
|-----|--------|
| `b` | Toggle bookmark on current node |
| `B` | Jump to next bookmarked node |

### Other

| Key | Action |
|-----|--------|
| `?` | Show stats screen for current tree |
| `Tab` | Open tree picker (switch to a different tree) |
| `D` | Delete the selected child subtree |
| `q` | Quit |
| `Esc` | Cancel overlays or generation; quit when idle |

## Tree picker

Press `Tab` to open the tree picker. Use `j`/`k` to navigate recent trees and `Enter` or `Tab` to switch to one.
Press `d` to delete the highlighted tree; the picker will confirm before deletion.

## Stats screen

Press `?` to open the stats overlay for the current tree. This shows:

- Total nodes and tree depth
- Path depth and generated-node count for the current path
- Model breakdown, including expansion, bookmark, and hide rates
- Descendant-score metrics: NPDS, win rate, DS, and DDS

## Configuration

The TUI keymap and defaults are configurable through TOML. See [[Configuration]].
