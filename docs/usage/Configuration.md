# Configuration

basemode-loom loads configuration from two TOML files, with project config overriding user config:

- `~/.config/basemode-loom/config.toml`
- `./.basemode-loom.toml`

## What you can configure

- Keybindings for the TUI
- Default model, max tokens, temperature, and branch count
- Per-model overrides for generation defaults
- Web-server origins, request/generation limits, and production behavior

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

[server]
allowed_origins = ["https://grove.example.com"]
max_message_bytes = 1048576
max_field_bytes = 262144
max_context_tokens = 32768
concurrent_generation_jobs = 1
max_branches_per_job = 8
generation_timeout_seconds = 120
max_output_tokens = 2000
```

Server settings can also be overridden with environment variables. Their names
are the upper-case setting names prefixed with `BASEMODE_LOOM_`, for example
`BASEMODE_LOOM_MAX_CONTEXT_TOKENS`. `BASEMODE_LOOM_ALLOWED_ORIGINS` accepts a
comma-separated list or a JSON string array. Environment values override TOML.

`basemode-loom serve --production` requires a non-empty explicit origin
allowlist and disables the API documentation surfaces. It does not expose
server settings through `/api/config`.

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
