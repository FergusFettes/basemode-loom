# Web Server

basemode-loom includes a FastAPI server for headless use or building custom frontends.

## Starting the server

```bash
basemode-loom serve --host 127.0.0.1 --port 8000
```

The default development mode enables docs, accepts missing WebSocket origins,
and automatically permits common loopback browser origins. It still binds only
to `127.0.0.1`. A non-loopback bind requires both `--host` and the explicit
`--public` acknowledgement.

For deployment, configure `[server]` in TOML or `BASEMODE_LOOM_*` environment
variables and use:

```bash
basemode-loom serve --production --host 127.0.0.1 --port 8010 \
  --db /var/lib/grove/grove.sqlite
```

Production startup fails when `allowed_origins` is empty. Production also
requires an allowed `Origin` on WebSocket handshakes and disables `/docs`,
`/redoc`, and `/openapi.json`; `--enable-docs` explicitly overrides the latter.
HTTP clients without an `Origin` remain supported. Origin enforcement is a
browser boundary, not authentication.

Or from Python:

```python
import uvicorn
from basemode_loom import GenerationStore
from basemode_loom.api import create_app

store = GenerationStore()
app = create_app(store)
uvicorn.run(app, host="127.0.0.1", port=8000)
```

### Embedding with a private store per user

The standalone CLI server loads one SQLite database at startup. An embedding
application can instead pass `store_resolver` to `create_app`. It receives
the trusted ASGI scope for each HTTP request and WebSocket connection and
returns the `GenerationStore` that connection may access:

```python
from starlette.types import Scope

from basemode_loom import GenerationStore
from basemode_loom.api import StoreResolver, create_app

default_store = GenerationStore("/var/lib/grove/default.sqlite")

def resolve_store(scope: Scope) -> GenerationStore:
    # Authentication middleware owned by the host application sets this.
    store_key = scope["state"]["grove_store_key"]
    return stores_by_key[store_key]

resolver: StoreResolver = resolve_store
app = create_app(default_store, store_resolver=resolver)
```

Use an authenticated application to derive the store key; never accept a
database path or store key from the browser. The supplied `default_store`
continues to be used when no resolver is passed, including by
`basemode-loom serve --db ...` for local frontend development.

## REST API

In development, FastAPI publishes the API contract automatically:

- `/openapi.json` contains the machine-readable OpenAPI 3 schema.
- `/docs` provides an interactive Swagger UI.
- `/redoc` provides a reference-oriented ReDoc view.

The schema includes query constraints and response models, so a frontend can
generate a typed client with tools such as `openapi-typescript`, Orval, or
OpenAPI Generator. Regenerate that client when `/openapi.json` changes.

### Search and filter trees

```
GET /api/trees?q=learning&category=research&source=codex&sort=auto&limit=50
```

Returns picker-ready tree summaries, the total match count, available facet
values and counts, and the database's search capabilities. Supported query
parameters are:

- `q`: ID, indexed keyword/semantic query, or metadata substring query when no
  retrieval index is available
- `category`, `domain`, `source`, `model`: repeatable facet values
- `sort`: `auto`, `relevance`, `recent`, `oldest`, `nodes`, or `name`
- `offset`, `limit`: pagination; `limit` is capped at 200

Values repeated within one facet are combined with OR. Different facets are
combined with AND. `auto` selects relevance ordering for indexed results and
recent ordering otherwise.

Each item includes its root and tree IDs, previews, node count, classification,
sources, models, and, for ranked searches, `score` and `best_node_id`. The
`search` object reports whether keyword and semantic retrieval are currently
available and explains missing optional dependencies.

### Manage embeddings

```
GET /api/embeddings
```

Reports whether the active database has a semantic index, including its model,
vector dimension, and indexed vector count.

```
POST /api/embeddings
Content-Type: application/json

{
  "model": "mlx",
  "min_chars": 1,
  "batch_size": 64,
  "incremental": true
}
```

Builds or incrementally updates the semantic index in the server's configured
database. `model` accepts `hash`, `mlx`, or an MLX/Hugging Face model ID. The
optional `dim` field controls the hash embedder dimension and defaults to 256.

The server must be installed with `basemode-loom[embed]` for hash indexes or
`basemode-loom[embed-mlx]` for MLX indexes. Index builds can take time and the
request remains open until the operation completes. The bundled nginx template
allows API requests to run for 15 minutes; configure any other reverse proxy
and client timeout accordingly.

### Get config

```
GET /api/config
```

Returns the UI-safe merged user/project config currently loaded by the server.
Server security settings are deliberately omitted.

### List trees

```
GET /api/roots
```

Returns a list of all root nodes as JSON objects.

### Create a root

```
POST /api/roots
Content-Type: application/json

{"text": "Your prompt here"}
```

Optional fields:

- `name`
- `model`
- `max_tokens`
- `temperature`
- `n_branches`
- `context`

### Delete a tree

```
DELETE /api/roots/{root_id}
```

### Get full tree

```
GET /api/roots/{root_id}/tree
```

Returns `{"nodes": [...]}` with all nodes in the tree.

### Get tree stats

```
GET /api/roots/{root_id}/stats
```

Returns the same stats as `basemode-loom stats`.

### Export a tree

```
GET /api/roots/{root_id}/export
```

Returns a JSON export payload with `version` and `nodes`.

### Get one node

```
GET /api/nodes/{node_id}
```

Returns the serialized node plus reconstructed `full_text`.

### List available models

```
GET /api/models
```

Returns the currently available `basemode` model catalog for picker UIs.

### Import a tree

```
POST /api/import
Content-Type: application/json

{"nodes": [...]}
```

Imports node records directly and returns the inserted count.

### Provider API keys

Keys are stored in basemode's own key file (`~/.config/basemode/auth.json`,
mode `0600`) through `basemode.keys`, so a key set here is the same key the
`basemode` CLI and the loom TUI use. A stored key takes effect immediately —
no restart — because the server also exports it into its own environment.

**Keys are write-only. No endpoint returns a stored key.** The only read
surface is the masked listing below.

```
GET /api/keys
```

```json
{
  "providers": [
    {
      "provider": "openai",
      "env_var": "OPENAI_API_KEY",
      "configured": true,
      "masked": "sk-p...6789",
      "source": "stored"
    }
  ],
  "writable": true
}
```

`source` is `"stored"` for a key in the key file and `"environment"` for one
exported into the server's environment, so a UI can explain why generation
already works without anything stored. `writable` reports whether this server
accepts writes.

```
PUT /api/keys/{provider}
Content-Type: application/json

{"value": "sk-..."}
```

Returns the same masked status object. The key travels in the body, not the
path or a query string, to keep it out of access logs and browser history.
`provider` must be one of basemode's known providers (`openai`, `anthropic`,
`openrouter`, `groq`, `gemini`, `together`, `moonshot`, `xai`, `zai`);
anything else is a `404 unknown_provider`, which also stops a caller from
naming an arbitrary environment variable. A blank value is `422 empty_key`
and a value over 8 KiB is `413 key_too_large`.

There is currently no delete endpoint: `basemode.keys` exposes no removal
function, and reimplementing its file format here would risk the two drifting
apart. Overwrite a key with `PUT`, or edit the auth file directly.

#### Who is allowed to write keys

The key file is machine-wide, so a key written through this API becomes the
key *every* caller of this server generates with. That is what a local
single-user tool wants and is wrong for a shared deployment, so writes are
**disabled by default whenever `production` is on** — `PUT` returns
`403 credential_writes_disabled` and `GET` reports `"writable": false`.

Origin enforcement is a browser boundary, not authentication, so a production
server should keep writes off unless it is genuinely single-user and
network-isolated. Override in either direction with:

```toml
[server]
allow_credential_writes = true
```

or `BASEMODE_LOOM_ALLOW_CREDENTIAL_WRITES=true`.

## WebSocket API

For live interactive sessions, connect to the WebSocket endpoint:

```
ws://localhost:8000/ws/session
```

The WebSocket handler manages a full `LoomSession`. On connection, send an init message to specify which tree to open:

```json
{"type": "init", "root_id": "<root_id>"}
```

The server then streams state updates and generation events back to the client as JSON.

Generation jobs are rejected rather than queued when global capacity is full;
the server sends a typed `generation_busy` message. It similarly sends
`generation_limit_exceeded` before contacting a provider when the complete
enabled model plan exceeds the branch or output-token limit. Message/field
limits, assembled-prompt limits, and a whole-job timeout provide additional
guards. Provider exception details are not returned to clients.

### Client messages

```json
{"type": "navigate", "direction": "child"}
{"type": "navigate", "direction": "parent"}
{"type": "navigate", "direction": "next_sibling"}
{"type": "navigate", "direction": "prev_sibling"}
{"type": "generate"}
{"type": "cancel"}
{"type": "edit", "original": "...", "edited": "..."}
{"type": "edit_node", "node_id": "...", "text": "..."}
{"type": "add_node", "parent_id": "...", "text": "..."}
{"type": "bookmark_toggle"}
{"type": "bookmark_next"}
{"type": "view_toggle"}
{"type": "hoist_toggle"}
{"type": "model_names_toggle"}
```

`edit` rewrites the whole current lineage and forks wherever the text changed.
`edit_node` targets one node instead: it forks that node alone (rewriting the
root in place, since a root has nothing to fork from) and checks the result
out. `add_node` hangs a hand-written child off `parent_id`, tagged
`manual`/`manual` rather than a model, and checks it out. Both reply with a
`state` message.

Config updates use `set_params`:

```json
{
  "type": "set_params",
  "model": "gpt-4o-mini",
  "max_tokens": 200,
  "temperature": 0.9,
  "n_branches": 3,
  "global_max_tokens": 200,
  "global_n_branches": 3,
  "context": "",
  "rewind_split_tokens": true,
  "show_model_names": true,
  "model_plan": [
    {
      "model": "gpt-4o-mini",
      "n_branches": 2,
      "max_tokens": 200,
      "temperature": 0.9,
      "enabled": true,
      "pinned_settings": true
    }
  ],
  "persist": true
}
```

Validation constraints:

- `max_tokens`: `50`-`8000`
- `temperature`: `0.0`-`2.0`
- `n_branches`: `1`-`64`
- `global_max_tokens`: `10`-`8000`
- `global_n_branches`: `1`-`64`
- `rewind_split_tokens`: boolean
- `model_plan`: non-empty list

### Server messages

- `state`: full serialized `SessionState`
- `token`: streamed token with `model_idx`, `branch_idx`, and `slot_idx`
- `generation_complete`: includes `new_nodes`
- `generation_error`: one per failed provider branch when a batch partially
  fails; includes the model, model/branch/slot indices, diagnostic category,
  and any finish reason or provider status
- `generation_cancelled`
- `tree_named`: emitted when a root gets auto-named
- `error`: protocol or validation error

The WebSocket exposes interactive navigation and generation. Tree discovery,
search, filtering, and deletion use the REST endpoints above.
