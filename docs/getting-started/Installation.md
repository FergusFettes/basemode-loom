# Installation

## From PyPI

```bash
pip install basemode-loom
```

## From source

```bash
git clone https://github.com/fergus/basemode-loom
cd basemode-loom
uv install
```

## Dependencies

basemode-loom depends on:

| Package | Purpose |
|---------|---------|
| `basemode` | Core LLM generation strategies |
| `textual` | Terminal UI framework |
| `fastapi` + `uvicorn` | Web API server |
| `litellm` | Auto-naming via cheap LLM call |

## Verifying the install

```bash
basemode-loom --help
```

You should see the CLI help output listing all available commands.
