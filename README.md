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

## Tree Search

The TUI tree picker (`Tab`) supports live metadata filtering, category/domain/
source/model facets, ID lookup, FTS5 keyword search, and semantic search when
the selected database contains a compatible vector index.

Guardian Angel corpora use the optional MLX search dependencies:

```bash
pip install 'basemode-loom[embed-mlx]'
basemode-loom view --db /path/to/corpus.sqlite
```

Press `/` in the tree picker, enter a query, and press `Enter` to rank matching
trees. Build or update an embedding index directly with:

```bash
basemode-loom embed --db /path/to/corpus.sqlite --model mlx
basemode-loom embed --db /path/to/corpus.sqlite --model mlx --incremental
```

The MLX model is downloaded lazily on first use. Embedding dependencies remain
isolated in the optional `embed` and `embed-mlx` installation extras.

## Model Selection

- TUI model picker is available via `m`.
- Picker can consume verified model metadata from `basemode` when available.
- Session state supports model-plan metadata for multi-model generation workflows.
- Force OpenRouter passthrough for new/unknown IDs with `-m "or:vendor/model"` (or `openrouter:vendor/model`), e.g. `-m "or:moonshotai/kimi-k2.6"`.

## Storage

By default, data is stored in a local SQLite DB under your user data directory.
Use `--db /path/to/file.sqlite` to choose a custom location.

Publish privacy-safe aggregate model statistics to basemode's shared evidence
store with `basemode-loom publish-evidence`. Use `--dry-run` to inspect the
payload; raw tree text and identifiers are never published.

## Docs

Project docs live in `docs/` (MkDocs):

```bash
make docs
make docs-serve
```

Then open `http://localhost:8001`.
