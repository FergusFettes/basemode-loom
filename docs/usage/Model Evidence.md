# Call evidence and local quality data

Basemode records content-free endpoint call outcomes locally. Each continuation
started by Loom carries only the source name `loom`, the installed Loom version,
and an ineligible contribution default. Basemode owns endpoint identity,
attempts, retries, timing, usage, finish reasons, failure classification, and
the persisted contribution preference.

Prompts, generated text, database paths, trees, nodes, sessions, accounts,
flags, and edits are not attached to these call observations. Loom retains the
product data it needs in `generations.sqlite`, including local flags, edits,
generation relationships, model plans, timing, usage, and cost information.

Public call-evidence contribution is an explicit basemode workflow. Preview
what basemode would contribute before exporting or opening a pull request:

```bash
basemode contribute preview
basemode contribute export
basemode contribute pr
```

Loom no longer publishes corpus-quality aggregates. If a quality export is
introduced later, it will use a separate optional schema and privacy review.
Model thumbs remain separate user-quality information.
