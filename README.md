# basemode-loom

Persistent branching exploration for LLM continuations.

`basemode-loom` lets you generate multiple continuations, navigate a tree of alternatives, and keep everything in a local SQLite store so you can resume later.

## Install

```bash
pip install basemode-loom
```

## Quickstart

```bash
# Create a new tree with 3 branches
basemode-loom run "The ship rounded the headland and" -n 3 -m gpt-4o-mini

# Open the interactive explorer
basemode-loom view

# Continue from selected branch
basemode-loom continue -b 2 -n 3
```

## Core Commands

```bash
basemode-loom --help
basemode-loom view --help
basemode-loom run --help
basemode-loom continue --help
basemode-loom stats --help
basemode-loom serve --help
```

Useful commands:

- `basemode-loom view`: interactive TUI tree explorer
- `basemode-loom run`: create a new tree from a prompt
- `basemode-loom continue`: branch from current/selected node
- `basemode-loom nodes|active|show|children`: inspect stored trees
- `basemode-loom stats`: analyze tree depth/branching/model usage
- `basemode-loom export|import`: move trees in/out as JSON/Markdown
- `basemode-loom serve`: run REST/WebSocket API for frontend usage

## Model Selection

- TUI model picker is available via `m`.
- Picker can consume verified model metadata from `basemode` when available.
- Session state supports model-plan metadata for multi-model generation workflows.

## Storage

By default, data is stored in a local SQLite DB under your user data directory.
Use `--db /path/to/file.sqlite` to choose a custom location.

## Docs

Project docs live in `docs/` (MkDocs):

```bash
make docs
make docs-serve
```

Then open `http://localhost:8001`.
