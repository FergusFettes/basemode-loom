# Changelog

Notable changes to basemode-loom are recorded here. The project follows
[Semantic Versioning](https://semver.org/); changes not yet released are listed
under Unreleased.

## Unreleased

### Added

- `basemode-loom import` adds trees and nodes from another database or from a
  JSON export, skipping everything already present. Node and tree ids are
  uuids, so a tree keeps its identity between machines: an import only ever
  adds, never rewrites, and re-running one is a no-op. A node joining a tree
  that already exists locally arrives with its checked-out flag cleared, so an
  import cannot move where this database was last reading; nodes in a tree that
  is new here keep theirs. `--dry-run` reports the plan per tree without
  writing, and `--tree` limits it to named trees.

- A generated node records the opening of its stream under `boundary` when
  basemode's healing rewrote it — `raw` as the provider sent it, `streamed`
  as the stream repair passed it on — so a botched seam can be attributed to
  the model or to the repair after the fact. Nothing is stored when healing
  left the opening alone, which is the overwhelming majority of generations.
  Surfaced on `GET /api/flags`.

- A generation can be flagged as bad: `flag_node` over the websocket sets
  `flagged` on the node the way a bookmark does, and `GET /api/flags` reads
  the flagged ones back paired with the text each model was continuing from,
  plus per-model flag counts weighed against how much that model was used.

### Changed

- Navigation on a deep tree is roughly twenty times faster. A session state
  snapshot read the node's ancestry by walking parent links one query at a
  time — two SQLite connections and three statements per ancestor — and did it
  five times over, once each for the full text, the context, the segments and
  the prompt entries. The ancestry is now one recursive query, read once per
  snapshot and shared. On a 1109-node tree at depth 171 that takes `get_state`
  from ~270ms to ~12ms. Connections also stop re-asserting `journal_mode` on
  every open; the journal mode belongs to the database file and is set once.

- Checking out a node no longer walks its ancestry a level at a time. Reading
  each level's siblings and writing each level's checked-out flag separately
  cost six connections and a transaction per ancestor; the siblings are now one
  query and the flags one transaction. Over a websocket, checking out a node at
  depth 170 goes from ~253ms to ~35ms.

- Generating no longer moves the reader. A finished branch is saved and left
  for the user to pick, rather than becoming the checked-out child and (when
  the batch held one continuation) the current node. Since branches now save
  as they land, that rule fired once per branch, so a multi-branch generation
  handed the reader to whichever provider answered first and flipped the
  checked-out child again as the rest arrived.

- One websocket connection may now have several generations in flight at once
  (`concurrent_generations_per_session`, default 3) instead of being locked to
  one at a time, so a slow provider in one place in the tree no longer blocks
  starting a continuation somewhere else. The server-wide
  `concurrent_generation_jobs` still caps everything. Over the cap the client
  gets a `generation_busy` error naming the limit.

- Generation now saves each branch the moment it finishes and emits a
  `branch_complete` event (followed by a state push over the websocket),
  instead of holding every completion back until the slowest branch in the
  batch returns. A finished continuation is immediately a real, selectable
  child. Branches that finished before a cancel are kept.

- A tree with no configured model plan now starts pinned to the global
  branches/tokens. `set_n_branches` and `set_max_tokens` write through a pinned
  entry to those globals, so setting them from the TUI or CLI still takes
  effect.

### Fixed

- Importing a tree that uses a context no longer fails on a foreign key.
  `import_nodes` ordered parents before children but ignored `context_id`,
  which is a foreign key too, so a node could be inserted ahead of the context
  it points at.

- Editing a root node's text no longer starts a whole new tree containing only
  the current path, stranding every other branch on the old tree. A root has no
  sibling position to fork into, so its text is now rewritten in place.

- Editing text now checks out the rewritten branch and moves the edited node's
  whole subtree across to it, instead of leaving the checked-out path on the
  pre-edit nodes and stranding their continuations. A mid-lineage edit rewrites
  only the nodes whose own text changed; the nodes below keep their identity
  rather than being duplicated onto the new branch.

## 0.5.3 - 2026-08-22

### Added

- An opt-in live-provider integration test for the loom generation lifecycle.

### Changed

- Centralize model-plan validation and persistence normalization.
- Isolate SQLite schema creation and migrations from the store repository.
- Make embedding API requests deployment-safe for longer-running index builds.
- Tighten CI dependency locking, caching, permissions, and job timeouts.

## 0.5.2 - 2026-08-22

### Added

- Archive and restore trees, including a tree-topology metrics module.
- Optional one-off OpenAI image generation for a branch.

### Changed

- Require Basemode 0.1.14 or newer, which supplies per-model generation
  quirks and safely omits unsupported temperature parameters.

### Fixed

- Treat empty provider completions as failed branches rather than saving empty
  nodes.
- Keep the shuffled-completion test independent of Basemode's completion
  normalisation behaviour.

## 0.5.1 - 2026-08-21

### Changed

- Require Basemode 0.1.11 or newer, picking up DeepSeek as a first-class
  provider and live provider data patched into the model listing.

## 0.5.0 - 2026-08-20

### Added

- `GET /api/models` now accepts `provider`, `search`, `available`, `verified`,
  and `since` query params, passed through to basemode's model picker.

### Changed

- Require Basemode 0.1.9 or newer.
- Generation now always enforces basemode's `strict_max_tokens`, clipping
  streamed output to the requested token budget instead of letting a
  provider run past it.

## 0.4.1 - 2026-08-15

### Changed

- Require Basemode 0.1.7 or newer.
## 0.4.0 - 2026-08-14

### Added

- Write-only provider API key storage at `GET /api/keys` and
  `PUT /api/keys/{provider}`, so a frontend can configure credentials without
  hand-editing files. Keys are held in basemode's own key file
  (`~/.config/basemode/auth.json`, mode `0600`) and shared with the `basemode`
  CLI and the Loom TUI. No endpoint returns a stored key; the only read
  surface reports a masked preview and whether the key came from the key file
  or the environment.
- `server.allow_credential_writes` (and `BASEMODE_LOOM_ALLOW_CREDENTIAL_WRITES`)
  to control key writes. Writes default to enabled locally and disabled under
  `--production`, because the key file is machine-wide: a key stored through a
  shared server becomes the key every caller of that server generates with.

### Changed

- Tag releases now build and publish through GitHub Actions with PyPI OIDC
  trusted publishing; manual workflow runs exercise the build without
  publishing.
- Merging a version bump into `main` now tags and publishes that version
  automatically. Releasing is triggered by the version in `pyproject.toml`
  changing, so ordinary merges do nothing and pushing a `v*` tag by hand
  still works as before.

## 0.3.0 - 2026-08-11

### Added

- A weekly GitHub Actions release pipeline that tests newer Basemode releases,
  bumps Loom's patch version, and publishes it to PyPI and GitHub.
- Production server mode with exact HTTP/WebSocket origin allowlists,
  fail-closed configuration validation, request and generation safeguards,
  global concurrency control, timeouts, and sanitized incident diagnostics.
- Hardened nginx, systemd, and daily SQLite backup deployment templates for a
  private single-user Grove installation.

### Changed

- `serve` continues to bind to `127.0.0.1` by default and now requires
  `--public` before accepting a non-loopback bind.
- Production mode disables the OpenAPI documentation surfaces unless they are
  explicitly enabled.

## 0.2.1 - 2026-08-11

### Added

- `basemode-loom embed` for full or incremental sqlite-vec index construction,
  compatible with Guardian Angel's `nodes_vec` and `vec_meta` schema.
- Typed embedding status and build endpoints at `GET /api/embeddings` and
  `POST /api/embeddings`, published in the generated OpenAPI schema.

## 0.2.0 - 2026-08-11

### Added

- Searchable full-screen tree picker with category, domain, source, and model
  facets; persistent filters; multiple sort modes; richer previews; and
  confirmed tree deletion.
- Hybrid tree search over node and tree IDs, FTS5 keyword indexes, and optional
  sqlite-vec semantic indexes. Keyword and semantic rankings are combined and
  node matches are rolled up to their owning trees.
- Optional `embed` and `embed-mlx` dependency groups for querying compatible
  embedding indexes, including Guardian Angel corpora.
- Prompt inspection screens for reviewing the messages and continuation
  strategy used for generation.
- Persisted per-tree generation settings.
- Structured application logging and diagnostic coverage around generation,
  storage, and WebSocket workflows.
- Typed `/api/trees` catalog endpoint with hybrid search, repeatable facets,
  sorting, pagination, facet counts, and capability reporting in OpenAPI.

### Changed

- Generation settings now live with their tree and flow through the CLI, API,
  TUI, import/export, and session layers.
- Node persistence was simplified by removing legacy storage fields and using
  tree metadata for shared configuration.
- Tree summaries and API serialization now expose the metadata required by the
  picker and search UI.

## 0.1.3 - 2026-04-21

### Added

- Multi-model selection and model-plan metadata.
- Expanded project documentation and published MkDocs site.
- Apache-2.0 license and package metadata.

### Fixed

- Hoist-toggle key handling and empty model-list behavior in the TUI.
