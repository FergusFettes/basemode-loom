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

## WebSocket API

For live interactive sessions, connect to the WebSocket endpoint:

```
ws://localhost:8000/ws/session
```

The WebSocket handler manages a full `LoomSession`. On connection, send an init message to specify which tree to open:

```json
{"type": "init", "root_id": "<root_id>"}
```

The server then streams state updates and generation events back to the client as JSON. Navigation and generation commands are sent as messages to the server.

This is the same session interface used internally by the TUI, so any command that works in the TUI is available over WebSocket.
