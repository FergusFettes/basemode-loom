# Changelog

Notable changes to basemode-loom are recorded here. The project follows
[Semantic Versioning](https://semver.org/); changes not yet released are listed
under Unreleased.

## Unreleased

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
