# CLI Reference

All commands are under `basemode-loom`. Run `basemode-loom --help` or `basemode-loom <command> --help` for up-to-date flags.

The CLI defaults to `view`, so `basemode-loom some-id-or-text` behaves like `basemode-loom view some-id-or-text`.

## Generation

### `run`

Generate continuations from a new prompt.

```bash
basemode-loom run "Your prompt here" [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-m`, `--model` | `gpt-4o-mini` | Model to use |
| `-M`, `--max-tokens` | `200` | Max tokens per continuation |
| `-n`, `--branches` | `1` | Number of parallel continuations |
| `-t`, `--temperature` | `0.9` | Sampling temperature |
| `-s`, `--strategy` | auto | Force a basemode continuation strategy |
| `--rewind` | `false` | Rewind short trailing word fragments before generation |
| `--show-strategy` | `false` | Print the resolved strategy before generation |
| `--show-usage` | `false` | Print token usage estimates after generation |
| `--show-cost` | `false` | Print estimated cost after generation |
| `--db` | default DB | Use a custom SQLite database path |

If `prefix` is omitted, `run` will read from stdin.

### `continue`

Generate continuations from the current active node.

```bash
basemode-loom continue [OPTIONS]
```

Uses the same model/settings as the active node's tree unless overridden.

| Option | Description |
|--------|-------------|
| `-b`, `--branch` | Branch index to continue from (default: checked-out child) |
| `-m`, `--model` | Override the active tree model |
| `-n`, `--branches` | Number of continuations |
| `-M`, `--max-tokens` | Override max tokens |
| `-t`, `--temperature` | Override temperature |
| `-s`, `--strategy` | Override strategy |
| `--rewind` | Rewind short trailing word fragments before generation |
| `--show-strategy` / `--show-usage` / `--show-cost` | Print generation diagnostics |
| `--db` | Use a custom SQLite database path |

## Navigation

### `view`

Open the interactive TUI explorer.

```bash
basemode-loom view [NODE_ID_OR_FILE]
```

If given a node ID or file path, opens that tree. Otherwise opens the most recently active tree. See [[TUI Guide]] for keybindings.

`source` can be:

- A node ID or unique node-ID prefix
- A `.txt` file, which is imported as a root prompt if needed
- A `.json` loom export, which is imported into the store
- Literal text, which becomes a new root unless an identical root already exists

If no argument is provided and stdin is piped, `view` uses stdin as the source text.

### `nodes`

List recent nodes.

```bash
basemode-loom nodes [-n 20]
```

### `active`

Show the current active node summary and metadata.

```bash
basemode-loom active
```

### `show`

Show a specific node.

```bash
basemode-loom show <NODE_ID>
```

Supports prefix matching — you can use the first few characters of a node ID.
Pass `--segment` to print only the node's own text segment instead of the reconstructed full text.

### `children`

List the children of a node.

```bash
basemode-loom children <NODE_ID>
```

### `roots`

List all root nodes.

```bash
basemode-loom roots
```

Shows root ID, optional generated name, child count, created time, and a text preview.

### `select`

Mark a node as the active cursor.

```bash
basemode-loom select <NODE_ID>
```

## Analysis

### `stats`

Show quantitative statistics for a tree.

```bash
basemode-loom stats [NODE_ID] [--json] [--file EXPORT.json]
```

Includes tree depth, path stats, model breakdown, bookmark/hide rates, and descendant-score metrics. Pass `--json` for machine-readable output, or `--file` to analyze a JSON export without using the SQLite store.

## Export

### `export`

Export a tree to a file or stdout.

```bash
basemode-loom export --to json
basemode-loom export --node <NODE_ID> --to file.md
basemode-loom export --node <NODE_ID> --to output.json
```

Supported targets:

- `json` for JSON to stdout
- `md` or `markdown` for Markdown to stdout
- A file path ending in `.json`, `.md`, or `.markdown`

JSON export serializes the full tree. Markdown export writes the currently checked-out path text for that tree.

There is no standalone `import` CLI command in the current implementation. To import:

- Open a `.json` export through `basemode-loom view export.json`
- Use the REST `POST /api/import` endpoint

## Server

### `serve`

Start the FastAPI web server.

```bash
basemode-loom serve [--host 127.0.0.1] [--port 8000]
```

See [[Web Server]] for API documentation.

## Storage

By default, basemode-loom uses `~/.local/share/basemode/generations.sqlite`.

- Override it per command with `--db /path/to/file.sqlite`
- Override it globally with `BASEMODE_DB=/path/to/file.sqlite`
