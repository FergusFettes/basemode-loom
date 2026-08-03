# Installation

## From PyPI

```bash
pip install basemode-loom
```

## From source

```bash
git clone https://github.com/FergusFettes/basemode-loom
cd basemode-loom
uv sync
```

## Semantic search

Semantic search is optional. It is available when the selected SQLite database
already contains a `nodes_vec` index and matching `vec_meta` metadata.

On Apple silicon, install the MLX embedding backend used by Guardian Angel
corpora:

```bash
pip install 'basemode-loom[embed-mlx]'
```

From a source checkout:

```bash
uv sync --extra embed-mlx
```

If a database uses the built-in deterministic hash embedder, the lighter
`embed` extra is sufficient. These extras query an existing vector index; they
do not create embeddings for a normal loom database.

## Dependencies

basemode-loom depends on:

| Package | Purpose |
|---------|---------|
| `basemode` | Core LLM generation strategies |
| `textual` | Terminal UI framework |
| `fastapi` + `uvicorn` | Web API server |
| `litellm` | Auto-naming via cheap LLM call |
| `sqlite-vec` + `mlx-embeddings` | Optional semantic search over indexed corpora |

## Verifying the install

```bash
basemode-loom --help
```

You should see the CLI help output listing all available commands.
