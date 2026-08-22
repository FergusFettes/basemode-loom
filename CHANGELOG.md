# Changelog

Notable changes to basemode-loom are recorded here. The project follows
[Semantic Versioning](https://semver.org/); changes not yet released are listed
under Unreleased.

## Unreleased

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
