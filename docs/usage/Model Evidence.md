# Model Evidence

Loom keeps trees, generated text, edits, and node identifiers in its private
`generations.sqlite` database. It can publish aggregate model-quality evidence
to basemode's shared model evidence store:

```bash
basemode-loom publish-evidence
```

The publication contains counts grouped by model, prompt method, tree-depth
bucket, and issue kind. It includes timing summaries and separates corrected
boundary edits from unresolved manual flags. It never includes tree or node
IDs, prompts, generated text, or edit contents.

Preview exactly what would be published:

```bash
basemode-loom publish-evidence --dry-run
```

Use ISO-8601 timestamps for incremental windows:

```bash
basemode-loom publish-evidence \
  --since 2026-08-01T00:00:00Z \
  --until 2026-09-01T00:00:00Z
```

By default, Loom derives a stable, non-reversible local corpus identifier from
the machine and database path. Set `--source-instance` to use an explicit label.
The evidence database is owned and migrated by basemode; Loom writes through
basemode's public API rather than opening that SQLite database directly.

Model thumbs also use the shared basemode evidence API. Loom retains a fallback
for older basemode releases, whose ratings are stored in `auth.json`.
