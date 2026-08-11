# Changelog

Notable changes to basemode-loom are recorded here. The project follows
[Semantic Versioning](https://semver.org/); changes not yet released are listed
under Unreleased.

## Unreleased

### Added

- `basemode-loom embed` for full or incremental sqlite-vec index construction,
  compatible with Guardian Angel's `nodes_vec` and `vec_meta` schema.

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
