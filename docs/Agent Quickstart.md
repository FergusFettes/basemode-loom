# Agent Quickstart

This page is written for LLM agents (like Claude Code) dropped into this repo. It gives you the mental model and a reading map so you can orient fast.

## What this repo is

**basemode-loom** is a persistent branching exploration tool for LLM text generation. Users point it at a text prompt, generate multiple continuations in parallel, navigate the resulting tree, and keep exploring from any node. Everything is stored in a local SQLite database.

It has three interfaces over a shared core:
- A **TUI** (Textual) for interactive exploration
- A **CLI** (Typer) for scripting and quick generation
- A **web server** (FastAPI + WebSocket) for headless use

## Dependency: basemode

basemode-loom depends on `basemode` (sibling package at `../basemode`) for all actual LLM calls. basemode handles strategy detection, streaming, and token boundary healing. If you're debugging generation behavior, start there. See [[basemode Overview]].

## Repo layout

```
src/basemode_loom/
├── store.py          ← SQLite layer; Node dataclass; all reads/writes
├── session.py        ← LoomSession; navigation, generation, state machine
├── display.py        ← UI-agnostic rendering; DisplayLine output
├── cli.py            ← Typer CLI (~1000 lines; all commands here)
├── naming.py         ← Auto-names trees via cheap LLM call
├── loom_formats.py   ← Legacy format parser for import
├── stats.py          ← Tree analysis and quality metrics
├── api/              ← FastAPI app, REST endpoints, WebSocket handler
└── tui/              ← Textual app, screens, widgets
```

## The three-layer rule

UI layers (TUI, web, CLI) **never** call `GenerationStore` directly. All interaction goes through `LoomSession`. This is an invariant enforced by convention — if you're adding a new UI feature, keep it.

```
UI (tui/, api/, cli.py)
    ↓  reads SessionState, calls methods
LoomSession (session.py)
    ↓  reads/writes nodes
GenerationStore (store.py)
```

## Key invariants

- **Nodes are immutable.** `Node` is a frozen dataclass. Only `metadata` can be updated via `store.update_metadata()`.
- **Text is stored per-segment.** Each node stores only its own continuation text, not the full prefix. `store.full_text(id)` reconstructs by walking lineage.
- **State is explicit.** The `state` table in SQLite tracks `active_node_id` and `checked_out:{parent_id}` — which child was selected at each parent. These persist across restarts.
- **Generation events are a union type.** `generate()` yields `TokenReceived | GenerationComplete | GenerationError | GenerationCancelled` — always match all cases.

## Where to read next, by task

| Task | Read first | Then |
|------|-----------|------|
| Add a new CLI command | `cli.py` | `session.py` for available methods |
| Add a new TUI feature | `tui/screens/loom.py` | `tui/widgets/loom_view.py`, `session.py` |
| Change how text is displayed | `display.py` | `tui/widgets/loom_view.py` to see how DisplayLine is consumed |
| Change persistence schema | `store.py` | Check `session.py` for any queries that'll need updating |
| Add a new generation event type | `session.py` (GenerationEvent union) | `tui/widgets/stream_view.py`, `api/_ws.py` for consumers |
| Add a REST endpoint | `api/_rest.py` | `api/_serialize.py` for Pydantic models |
| Debug import of legacy files | `loom_formats.py` | `cli.py` (`import` command) |
| Understand stats/metrics | `stats.py` | `tui/screens/stats.py` for display |
| Add auto-naming behavior | `naming.py` | Called from `session.py` after generation |

## Common patterns

**Reading the current state:**
```python
state = session.get_state()
state.current_node      # Node
state.full_text         # str
state.children          # list[Node]
state.selected_child_idx
```

**Iterating over generation events:**
```python
async for event in session.generate():
    match event:
        case TokenReceived(branch_idx=i, token=t): ...
        case GenerationComplete(new_nodes=nodes): ...
        case GenerationError(error=e): ...
        case GenerationCancelled(): ...
```

**Querying the store directly** (only from session.py or tests):
```python
store.full_text(node_id)
store.lineage(node_id)   # root-first list
store.children(node_id)
store.tree(root_id)      # all nodes breadth-first
```

**Metadata conventions:**
Root nodes carry tree-level config in `metadata`: `model`, `max_tokens`, `temperature`, `n_branches`, `context`, `name`. Node-level metadata carries `bookmarked` and optionally `rating`.

## Tests

```bash
uv run pytest tests          # all tests with coverage
uv run pytest -m integration # integration tests only
```

Tests live in `tests/`. Store tests use a temp SQLite file. TUI tests are limited — the Textual framework makes unit testing widgets awkward, so display logic in `display.py` is tested directly instead.

## Docs

```bash
make docs        # build site/ 
make docs-serve  # live reload at localhost:8001
```

Docs source is in `docs/` as Obsidian-compatible markdown with wikilink syntax. File names match link targets (unique across the vault).
