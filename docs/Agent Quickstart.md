# Agent Quickstart

Use this page when changing the `basemode-loom` repository. It is about
working on the project; [[Quickstart]] is the guide for using the installed
CLI.

## First five minutes

```bash
uv sync --all-groups
make lint
make test
make docs
```

Run focused tests while iterating, then run `make release-check` before
handing off a release-facing change. It runs linting, the test suite, a strict
documentation build, and package builds. Integration tests make live provider
requests and are intentionally opt-in. Set a model with a configured provider
key before running them:

```bash
BASEMODE_LOOM_INTEGRATION_MODEL=gpt-4o-mini make test-integration
```

Never print or commit provider credentials.

## Repository map

| Area | Start here | Use it when |
|---|---|---|
| Persistence and tree model | `src/basemode_loom/store.py` | Changing SQLite schema, nodes, trees, or tree queries |
| Session and generation | `src/basemode_loom/session.py` | Changing navigation, generation lifecycle, or session state |
| Command-line interface | `src/basemode_loom/cli.py` | Adding or adjusting CLI commands |
| TUI | `src/basemode_loom/tui/` | Changing interactive exploration screens and widgets |
| REST and WebSocket API | `src/basemode_loom/api/` | Adding endpoints or changing server behaviour |
| Rendering | `src/basemode_loom/display.py` | Changing UI-agnostic text layout |
| Configuration and credentials | `config.py`, `credentials.py`, `keymap.py` | Changing defaults, server limits, keys, or bindings |
| Retrieval | `src/basemode_loom/retrieval/` | Changing semantic-index construction or search |
| Tree analysis | `stats.py`, `graph_stats.py` | Changing metrics and statistics views |
| Legacy import/export | `loom_formats.py`, `cli.py` | Supporting legacy loom data or export formats |
| Tests | `tests/` | Adding regressions beside the affected feature |

For public behaviour and examples, see [[CLI Reference]], [[TUI Guide]],
[[Python API]], and [[Web Server]]. Keep them in sync with code changes.

## Architecture

`GenerationStore` owns SQLite persistence. `LoomSession` is the stateful
navigation and generation layer used by the interactive loom screen. The CLI
and API also use the store directly for their query, import/export, and service
workflows, so do not assume all store access must flow through a session.

```text
TUI loom screen ──> LoomSession ──> GenerationStore
CLI commands ────────────────────> GenerationStore
REST / WebSocket API ────────────> GenerationStore
```

`basemode-loom` delegates model continuation to the sibling `basemode` project
when developing both checkouts. `basemode` owns provider strategy detection,
streaming, and token-boundary healing; loom owns persistent trees, navigation,
and its interfaces. Start in `../basemode` when debugging a raw continuation
or model compatibility issue.

## Key invariants

- `Node` is a frozen dataclass. Use store methods to make supported metadata or
  context changes; node text itself is never mutated in place.
- Nodes store one text segment; `store.full_text(node_id)` reconstructs the
  complete prefix from its lineage.
- The SQLite state table persists the active node and checked-out child per
  parent, so navigation survives restarts.
- `LoomSession.generate()` yields every member of `GenerationEvent`:
  `TokenReceived`, `GenerationComplete`, `GenerationError`, and
  `GenerationCancelled`. Consumers must handle all four.
- Inline and full-text edits create forks or a new edited lineage; they do not
  mutate an existing node's text.

## Common patterns

**Read the active session state:**

```python
state = session.get_state()
state.current_node
state.full_text
state.children
state.selected_child_idx
```

**Consume generation events:**

```python
async for event in session.generate():
    match event:
        case TokenReceived(model_idx=mi, branch_idx=bi, slot_idx=si, token=t): ...
        case GenerationComplete(new_nodes=nodes): ...
        case GenerationError(error=error): ...
        case GenerationCancelled(): ...
```

**Query a tree:**

```python
store.full_text(node_id)
store.lineage(node_id)       # root-first list
store.children(node_id)
store.tree_for_node(node_id) # tree containing this node
```

## Change checklist

1. Make the smallest coherent change and add or adjust a regression test.
2. Update the relevant user-facing docs and public API reference.
3. Run focused tests, then the appropriate `make` checks above.
4. Run `make release-check` before release-facing handoff.
5. Commit small logical units. Update `uv.lock` with `uv lock` whenever
   dependency constraints change.

## Further reading

- [[Installation]] — source setup and optional retrieval dependencies
- [[Configuration]] — TOML settings and environment overrides
- [[TUI Guide]] — interactive behaviour and keybindings
- [[Web Server]] — REST, WebSocket, and production-server behaviour
- [[basemode Overview]] — the continuation layer loom builds upon
