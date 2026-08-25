# Model Rating

`basemode-loom stats` scores nodes against their siblings. `basemode-loom
rating` uses those same choices to rate the models themselves, on one scale.

```bash
basemode-loom rating
```

## Why not just average the peer score

Every generation batch is a controlled comparison you already made: same
prompt, same context, same moment, several models. `stats` exploits that with
`normalized_peer_descendant_score` — a completion's share of the descendants
its batch produced, scaled so an even share is 1.0.

The catch is that the batch mean is 1.0 **by construction**, whatever was in
the batch. So a peer score of 1.44 does not mean "this model is good", it means
"this model beat whoever was standing next to it". Averaging that per model
compares scores computed against different reference populations. Three
consequences show up immediately in a real corpus:

- A model batched only against weak models looks strong.
- In a two-way batch the pair is *forced* to average exactly 1.0, so two strong
  models duelling each other both look average.
- A model generated on its own has a peer score pinned to 1.0 by definition.

`rating` fits a Bradley-Terry model over the pairwise comparisons the batches
already contain. That puts every model on one latent scale **even when two of
them never met**, by routing through shared opponents.

## Reading the output

The report leads with the comparison graph, not the ranking, because a ranking
without its graph is a confidence trick — the ordering between two models that
never met is an inference, and it can rest on a handful of comparisons.

- **Cohorts** — which line-ups were actually generated together. If these are
  near-disjoint, per-model peer scores are not comparable at all.
- **Comparison graph** — edges and their weights, whether the graph is
  connected, and any **cut edge** whose removal would disconnect it. A cut edge
  carrying five comparisons means everything across that split is decoration.
- **Ratings** — elo with a 95% interval from a cluster bootstrap that resamples
  whole *batches*, since the comparisons inside a batch are one decision.
- **Depth-conditional ratings** — separate fits per depth band. Models can and
  do swap places between opening a passage and sustaining one.
- **What is still unmeasured** — the standard error of each pairwise
  difference, widest first.
- **Generate this next** — the line-up that would shrink the ratings most.

Two diagnostics in the ratings footer are worth watching:

- `nu`, the tie parameter. Most loom comparisons are ties, because one sibling
  gets carried on from and the rest are never touched. A high `nu` means you
  were moving fast rather than comparing carefully — it measures the session as
  much as the models.
- `position bias`, in elo per slot. On one real corpus this fits at −43 elo per
  slot, larger than the gap between several adjacent models, favouring *later*
  slots. **Randomise the order completions are displayed in** and the effect
  disappears at the source.

## Choosing what to generate next

Rating precision here is limited by the *connectivity* of the comparison graph,
not by how much you write. A corpus that only ever batched A against B and C
against D says nothing about A versus C, and another thousand nodes will not
change that.

The final section scores candidate line-ups by how much they would shrink the
summed variance of every pairwise difference. Repeating a line-up you have
already run scores close to 1.00x — no information at all — while a line-up
straddling two cohorts that have never met can be worth several times more per
batch.

```bash
basemode-loom rating --cohort-size 5
```

## Options

| Option | Effect |
| --- | --- |
| `--root ID` | Restrict to these roots. Repeatable. |
| `--signal` | What counts as winning: `descendant` (default), `discounted`, `click`, `bookmark`. |
| `--models a,b,c` | Rate one cohort only. Required for depth-conditional ratings to be comparable across bands. |
| `--min-games N` | Hold out models with fewer comparisons (default 20). |
| `--depth-bands N` | Depth bands to fit separately. |
| `--raw-names` | Rate gateway variants separately instead of merging them. |
| `--keep-unjudged` | Keep batches where nothing was expanded. |
| `--json` | Machine-readable output. |

By default, batches nobody continued from are dropped: an all-zero batch is not
a k-way draw, it is the writer walking away. Gateway variants of one model
(`moonshot/kimi-k3` and `together_ai/moonshotai/kimi-k3`) are merged on the leaf
name, while dated pins (`-0731`) are kept separate.

## Caveats

Depth is confounded with time-in-session in any tree written straight through.
The report derives sittings from node timestamps so you can tell the two apart,
but only if you returned to a tree and branched from a shallow node. To measure
depth cleanly, run the same line-up at the top of several fresh trees.

Failed generations are not recorded — when a provider errors, no node is
created. A model that fails often is not penalised, it is simply absent, which
makes its sample smaller and possibly biased (timeouts correlate with long
prompts, so its surviving comparisons skew shallow).

## Python API

```python
from basemode_loom.rating import batches_from_store, comparisons_from_batches
from basemode_loom.rating.report import analyze, render
from basemode_loom.store import GenerationStore

batches = batches_from_store(GenerationStore(None))
report = analyze(batches, signal="descendant")
print(render(report))
```

`batches_from_tree` takes any `AnalysisTree`, so imported minihf, bonsai and
tinyloom trees can be rated the same way.
