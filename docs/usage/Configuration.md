# Configuration

basemode-loom loads configuration from two TOML files, with project config overriding user config:

- `~/.config/basemode-loom/config.toml`
- `./.basemode-loom.toml`

## What you can configure

- Keybindings for the TUI
- Default model, max tokens, temperature, and branch count
- Per-model overrides for generation defaults

## Example

```toml
[keys]
generate = "space"
quick_generate = "shift+space"
numeric_branch_shortcuts = true
open_picker = "tab"
open_stats = "?"

[defaults]
model = "gpt-4o-mini"
max_tokens = 200
temperature = 0.9
n_branches = 1
model_overrides = true

[model."gpt-4o"]
n_branches = 3

[model."claude-opus-4-7"]
n_branches = 2
temperature = 0.8
```

## Default keybindings

### Navigation

| Key | Action |
|-----|--------|
| `h` | Go to parent |
| `l` | Go to selected child |
| `j` | Select next child |
| `k` | Select previous child |
| `H` | Move the word cursor left within the selected child |
| `L` | Move the word cursor right / clear it |

### Generation and params

| Key | Action |
|-----|--------|
| `Space` | Generate from the current node |
| `Shift+Space` | Quick-generate with `+10` max tokens |
| `1`-`9` | Set branches per model directly |
| `m` | Open the model picker |
| `w` / `s` | Increase / decrease max tokens by `50` |
| `t` | Enter max tokens explicitly |
| `d` / `a` | Increase / decrease branches per model |

### Editing and views

| Key | Action |
|-----|--------|
| `e` | Inline-edit the selected child segment |
| `E` | Edit the current node segment in `$EDITOR` |
| `c` | Edit persisted context / system prompt |
| `v` | Toggle branch/tree view |
| `n` | Toggle model-name display |
| `Z` | Toggle hoist on the current node |
| `b` | Toggle bookmark |
| `B` | Jump to next bookmark |
| `Tab` | Open the tree picker |
| `?` | Open stats |
| `D` | Delete the selected child subtree |
| `Esc` | Cancel overlays or generation; quit when idle |
| `q` | Quit |

## Notes

- Config defaults affect new sessions; tree-specific settings are also persisted in root metadata as you work.
- Per-model overrides are applied by model ID, with a fallback match on the short name after the last `/`.
