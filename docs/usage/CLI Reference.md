# CLI Reference

All commands are under `basemode-loom`. Run `basemode-loom --help` or `basemode-loom <command> --help` for up-to-date flags.

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
| `-n`, `--n-branches` | `3` | Number of parallel continuations |
| `-t`, `--temperature` | `0.9` | Sampling temperature |

### `continue`

Generate continuations from the current active node.

```bash
basemode-loom continue [OPTIONS]
```

Uses the same model/settings as the active node's tree unless overridden.

| Option | Description |
|--------|-------------|
| `-b`, `--branch` | Branch index to continue from (default: checked-out child) |
| `-n`, `--n-branches` | Number of continuations |

## Navigation

### `view`

Open the interactive TUI explorer.

```bash
basemode-loom view [NODE_ID_OR_FILE]
```

If given a node ID or file path, opens that tree. Otherwise opens the most recently active tree. See [[TUI Guide]] for keybindings.

### `nodes`

List recent nodes.

```bash
basemode-loom nodes [-n 20]
```

### `active`

Show the current active node's full text and metadata.

```bash
basemode-loom active
```

### `show`

Show a specific node.

```bash
basemode-loom show <NODE_ID>
```

Supports prefix matching — you can use the first few characters of a node ID.

### `children`

List the children of a node.

```bash
basemode-loom children <NODE_ID>
```

## Analysis

### `stats`

Show quantitative statistics for a tree.

```bash
basemode-loom stats [NODE_ID] [--json]
```

Includes tree depth, expansion rates, model breakdown, and descendant scores. Pass `--json` for machine-readable output.

## Import / Export

### `export`

Export a tree to a file or stdout.

```bash
basemode-loom export [NODE_ID] --to json
basemode-loom export [NODE_ID] --to file.md
basemode-loom export [NODE_ID] --to output.json
```

Supported formats: `json`, `md` (markdown), or a file path with an appropriate extension.

### `import`

Import a tree from a file. Supports several legacy formats (tinyloom, minihf, bonsai, basemode-json).

```bash
basemode-loom import <FILE>
```

## Server

### `serve`

Start the FastAPI web server.

```bash
basemode-loom serve [--host 127.0.0.1] [--port 8000]
```

See [[Web Server]] for API documentation.
