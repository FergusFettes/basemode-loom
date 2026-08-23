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

### Model ratings

A user can rate a model up or down. Ratings are stored in basemode's own
config file (`~/.config/basemode/auth.json`) beside keys and pinned
strategies, not in the corpus database — a thumb belongs to the user, so it
survives a `--db` swap, is never exported with a tree, and is shared with the
`basemode` CLI and the loom TUI.

A rating only reorders listings: `GET /api/models` sorts thumbs-up models
first and thumbs-down models last, and every entry it returns carries a
`rating` field (`1`, `-1`, or `null`). Nothing is hidden, and generation is
unaffected.

```
GET /api/models/ratings
```

```json
{"ratings": {"openai/gpt-4o-mini": 1, "openai/gpt-4o": -1}, "writable": true}
```

```
GET /api/models/rating?model=gpt-4o-mini
```

```json
{"model": "gpt-4o-mini", "rating": 1}
```

```
PUT /api/models/rating
Content-Type: application/json

{"model": "gpt-4o-mini", "rating": 1}
```

`rating` is `1`, `-1`, or `null` to clear; anything else is `422
invalid_rating`, and a blank model is `422 empty_model`. The model ID travels
in the body because model IDs contain slashes (`anthropic/claude-opus-5`),
which a path parameter would force every caller to encode. The response
reports the resolved ID the thumb was stored under (`gpt-4o-mini` →
`openai/gpt-4o-mini`), normalized exactly as generation normalizes it, so the
same model rated by either name is one thumb.

Ratings are preferences rather than secrets, but they are still the
operator's preferences in a machine-wide file, so writes follow the same
default as key writes: **disabled whenever `production` is on**, with `PUT`
returning `403 rating_writes_disabled` and `GET` reporting
`"writable": false`. Override with:

```toml
[server]
allow_rating_writes = true
```

or `BASEMODE_LOOM_ALLOW_RATING_WRITES=true`.

### Flagged generations

A user can mark one generation as bad: `flag_node` over the websocket sets
`flagged` on the node, exactly like a bookmark, and toggles it off again.

It is deliberately a bare boolean rather than a reason code. The node and its
parent already carry everything needed to work out *what* went wrong — the
text on both sides of the seam the model continued from, the model, the
strategy, and the parameters it ran with — so the flag only has to mark which
generations are worth reading.

```
GET /api/flags
GET /api/flags?model=deepseek/deepseek-v4-flash&limit=20&prefix_chars=600
```

```json
{
  "flags": [
    {
      "node_id": "...",
      "tree_id": "...",
      "parent_id": "...",
      "model": "deepseek/deepseek-v4-flash",
      "strategy": "system",
      "max_tokens": 200,
      "temperature": 0.9,
      "created_at": "2026-08-23T18:41:02Z",
      "prefix": "...the ship rounded the headland and",
      "prefix_truncated": true,
      "text": "the sea opened out.",
      "boundary": {"raw": " wield both. The sculptor does", "streamed": "wield both. The sculptor does"}
    }
  ],
  "by_model": {
    "deepseek/deepseek-v4-flash": {"generated": 40, "flagged": 12}
  }
}
```

Each entry pairs what the model was given with what it produced, so the join
between them reads directly — which is where a botched first word shows up.
`prefix` is the tail of the real generation prefix (the whole lineage, not
just the parent node's own text), trimmed to `prefix_chars`. `by_model`
counts flags against generated nodes for the same model, because three flags
out of three is a different signal from three out of three hundred.

`boundary` records the opening of the generation before it was healed, and is
present only when healing rewrote it — which is rare, so its presence is
itself a signal. `raw` is what the provider's stream opened with and
`streamed` is what survived basemode's stream repair; the node's own `text` is
the third point. Together they say whether a botched seam came from the model
or from the repair, which cannot be reconstructed afterwards from the stored
text alone. Nothing is stored for the overwhelming majority of generations,
where all three agree.

Unlike ratings and health, flags live in the corpus database rather than in
basemode's config, because the evidence does: a flag is worth nothing without
the node it points at, and it should travel with the tree when it is exported
or shared.

### Observed model health

A rating is an opinion; health is the record. Every generated branch is
recorded against the model it ran on — whether it produced usable text, and
if not, how it failed — in basemode's `~/.config/basemode/health.sqlite`. A
branch counts as a failure when the provider errors, when it returns nothing,
and when what it returned normalizes away to whitespace, which is a case only
the loom session can see.

```
GET /api/models/health
GET /api/models/health?model=gpt-4o-mini&days=7
```

```json
{
  "health": {
    "openai/gpt-4o-mini": {
      "model": "openai/gpt-4o-mini",
      "attempts": 84,
      "successes": 75,
      "failures": 9,
      "failure_rate": 0.1071,
      "last_category": "rate_limit",
      "last_status": 429,
      "last_failure_at": "2026-08-23T13:41:02.517841+00:00",
      "window_days": 7,
      "recent_attempts": 31,
      "recent_failures": 2,
      "recent_failure_rate": 0.0645,
      "categories": {"rate_limit": 2}
    }
  }
}
```

`model` is normalized the way generation normalizes it, and a model that has
never been generated with is simply absent rather than a `404`; a blank
`model` is `422 empty_model`. All-time totals are kept indefinitely, while
the category breakdown comes from an event log pruned after 30 days, so a
`days` window never reaches further back than that.

Entries from `GET /api/models` carry the same record in a `health` field
(`null` for a model never used); pass `health_days=` there to window it. The
failure categories match the `category` on a `generation_error` WebSocket
message, so a UI can label a live failure and the history with one vocabulary.

Recording is read-only from the API's point of view — there is no endpoint
that writes it, and it happens as a side effect of generating. Set
`BASEMODE_NO_HEALTH=1` in the server's environment to turn it off.

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
{"type": "delete_node", "node_id": "..."}
{"type": "bookmark_node", "node_id": "..."}
{"type": "flag_node", "node_id": "..."}
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
`manual`/`manual` rather than a model, and checks it out. Both take their
text straight from a person, so both restore a missing space between the new
segment and the text before it — except where the segment opens with closing
punctuation or a contraction, which attach to the previous word. Both reply
with a `state` message.

`delete_node` removes a node and its whole subtree, landing the cursor on the
parent; it refuses a root, which would take the tree with it. `bookmark_node`
toggles the bookmark on any node, where `bookmark_toggle` only reaches the
current one. `flag_node` toggles `flagged` on a node — see
[Flagged generations](#flagged-generations).

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
