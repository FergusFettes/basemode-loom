# Web Server

basemode-loom includes a FastAPI server for headless use or building custom frontends.

## Starting the server

```bash
basemode-loom serve --host 127.0.0.1 --port 8000
```

Or from Python:

```python
import uvicorn
from basemode_loom import GenerationStore
from basemode_loom.api import create_app

store = GenerationStore()
app = create_app(store)
uvicorn.run(app, host="127.0.0.1", port=8000)
```

## REST API

The FastAPI app exposes interactive docs at `/docs`.

### Get config

```
GET /api/config
```

Returns the merged user/project config currently loaded by the server.

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

### Client messages

```json
{"type": "navigate", "direction": "child"}
{"type": "navigate", "direction": "parent"}
{"type": "navigate", "direction": "next_sibling"}
{"type": "navigate", "direction": "prev_sibling"}
{"type": "generate"}
{"type": "cancel"}
{"type": "edit", "original": "...", "edited": "..."}
{"type": "bookmark_toggle"}
{"type": "bookmark_next"}
{"type": "view_toggle"}
{"type": "hoist_toggle"}
{"type": "model_names_toggle"}
```

Config updates use `set_params`:

```json
{
  "type": "set_params",
  "model": "gpt-4o-mini",
  "max_tokens": 200,
  "temperature": 0.9,
  "n_branches": 3,
  "context": "",
  "show_model_names": true,
  "model_plan": [
    {
      "model": "gpt-4o-mini",
      "n_branches": 2,
      "max_tokens": 200,
      "temperature": 0.9,
      "enabled": true
    }
  ],
  "persist": true
}
```

Validation constraints:

- `max_tokens`: `50`-`8000`
- `temperature`: `0.0`-`2.0`
- `n_branches`: `1`-`64`
- `model_plan`: non-empty list

### Server messages

- `state`: full serialized `SessionState`
- `token`: streamed token with `model_idx`, `branch_idx`, and `slot_idx`
- `generation_complete`: includes `new_nodes`
- `generation_error`
- `generation_cancelled`
- `tree_named`: emitted when a root gets auto-named
- `error`: protocol or validation error

This is the same session interface used internally by the TUI, so any command that works in the TUI is available over WebSocket.
