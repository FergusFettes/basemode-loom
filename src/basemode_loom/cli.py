import asyncio
import contextlib
import dataclasses
import ipaddress
import json as _json
import sys
from pathlib import Path
from typing import Annotated

import click
import typer
import typer.core
from basemode.keys import get_default_model
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .logging_utils import configure_logging, get_logger
from .model_resolver import resolve_model_id
from .store import AmbiguousNodeReference, GenerationStore, Node

log = get_logger(__name__)
console = Console()
_BRANCH_COLORS = ["green", "blue", "yellow", "magenta", "cyan"]


_GROUP_FLAGS = {"--help", "-h", "--install-completion", "--show-completion"}


def should_name(text: str) -> bool:
    from .naming import should_name as _should_name

    return _should_name(text)


def generate_name(text: str) -> str | None:
    from .naming import generate_name as _generate_name

    return _generate_name(text)


def _default_to(command: str) -> type:
    class _Group(typer.core.TyperGroup):
        def parse_args(self, ctx: click.Context, args: list) -> list:
            if not args or (args[0].startswith("-") and args[0] not in _GROUP_FLAGS):
                args = [command, *args]
            return super().parse_args(ctx, args)

        def resolve_command(self, ctx: click.Context, args: list) -> tuple:
            try:
                return super().resolve_command(ctx, args)
            except click.UsageError:
                args.insert(0, command)
                return super().resolve_command(ctx, args)

    return _Group


app = typer.Typer(
    help="Persistent branching exploration and SQLite-backed sessions.",
    cls=_default_to("view"),
)


@app.callback(invoke_without_command=True)
def _init_logging() -> None:
    configure_logging("cli")


async def _stream_one(
    prefix: str,
    model: str,
    max_tokens: int,
    temperature: float,
    strategy: str | None,
    rewind: bool = False,
) -> str:
    from basemode.continue_ import continue_text

    console.print(f"[dim]{prefix}[/dim]", end="")
    chunks: list[str] = []
    async for token in continue_text(
        prefix,
        model,
        max_tokens=max_tokens,
        temperature=temperature,
        strategy=strategy,
        rewind=rewind,
        strict_max_tokens=True,
    ):
        chunks.append(token)
        console.print(token, end="")
    console.print()
    return "".join(chunks)


async def _stream_branches(
    prefix: str,
    model: str,
    n: int,
    max_tokens: int,
    temperature: float,
    strategy: str | None,
    rewind: bool = False,
) -> list[str]:
    from basemode.continue_ import branch_text

    buffers: list[list[str]] = [[] for _ in range(n)]

    with Live(
        _branches_panel(prefix, buffers),
        console=console,
        refresh_per_second=12,
    ) as live:
        async for idx, token in branch_text(
            prefix,
            model,
            n=n,
            max_tokens=max_tokens,
            temperature=temperature,
            strategy=strategy,
            rewind=rewind,
            strict_max_tokens=True,
        ):
            buffers[idx].append(token)
            live.update(_branches_panel(prefix, buffers))

    return ["".join(buf) for buf in buffers]


def _branches_panel(prefix: str, buffers: list[list[str]]) -> Panel:
    columns = []
    for i, buf in enumerate(buffers):
        color = _BRANCH_COLORS[i % len(_BRANCH_COLORS)]
        text = Text(f"Branch {i + 1}\n", style=f"bold {color}")
        text.append("".join(buf), style=color)
        columns.append(text)
    prompt = Text("Prompt\n", style="bold")
    prompt.append(prefix, style="dim")
    return Panel(
        Group(
            prompt,
            Rule(style="dim"),
            Columns(columns, equal=True, expand=True),
        ),
        title="Branches",
        border_style="dim",
    )


@app.command("run")
def loom_run(
    ctx: typer.Context,
    prefix: Annotated[
        str | None, typer.Argument(help="Text to continue (or pipe via stdin)")
    ] = None,
    model: Annotated[str | None, typer.Option("-m", "--model")] = None,
    n: Annotated[
        int, typer.Option("-n", "--branches", help="Number of parallel continuations")
    ] = 1,
    max_tokens: Annotated[int, typer.Option("-M", "--max-tokens")] = 200,
    temperature: Annotated[float, typer.Option("-t", "--temperature")] = 0.9,
    strategy: Annotated[str | None, typer.Option("-s", "--strategy")] = None,
    rewind: Annotated[
        bool,
        typer.Option(
            "--rewind",
            help="Rewind short trailing word fragments before generation.",
        ),
    ] = False,
    show_strategy: Annotated[bool, typer.Option("--show-strategy")] = False,
    show_usage: Annotated[
        bool,
        typer.Option(
            "--show-usage", help="Show estimated token usage after generation"
        ),
    ] = False,
    show_cost: Annotated[
        bool, typer.Option("--show-cost", help="Show estimated cost after generation")
    ] = False,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Persist a generation tree in SQLite."""
    if prefix is None and not sys.stdin.isatty():
        prefix = sys.stdin.read()
    if prefix is None:
        console.print(ctx.get_help())
        return
    store = GenerationStore(db)
    _run_loom_generation(
        store,
        prefix,
        None,
        model,
        n,
        max_tokens,
        temperature,
        strategy,
        rewind,
        show_strategy,
        show_usage,
        show_cost,
    )


@app.command("continue")
def loom_continue(
    ctx: typer.Context,
    branch: Annotated[
        int | None,
        typer.Option(
            "-b", "--branch", min=1, help="Select a child branch of the active node"
        ),
    ] = None,
    model: Annotated[str | None, typer.Option("-m", "--model")] = None,
    n: Annotated[
        int, typer.Option("-n", "--branches", help="Number of parallel continuations")
    ] = 1,
    max_tokens: Annotated[int, typer.Option("-M", "--max-tokens")] = 200,
    temperature: Annotated[float, typer.Option("-t", "--temperature")] = 0.9,
    strategy: Annotated[str | None, typer.Option("-s", "--strategy")] = None,
    rewind: Annotated[
        bool,
        typer.Option(
            "--rewind",
            help="Rewind short trailing word fragments before generation.",
        ),
    ] = False,
    show_strategy: Annotated[bool, typer.Option("--show-strategy")] = False,
    show_usage: Annotated[
        bool,
        typer.Option(
            "--show-usage", help="Show estimated token usage after generation"
        ),
    ] = False,
    show_cost: Annotated[
        bool, typer.Option("--show-cost", help="Show estimated cost after generation")
    ] = False,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Continue from the stored active node."""
    store = GenerationStore(db)
    active = store.get_active_node()
    if active is None:
        console.print("[red]No active node stored yet.[/red]")
        raise typer.Exit(1)
    base_node = _resolve_loom_base(store, active, branch)
    prefix = store.full_text(base_node.id)
    _run_loom_generation(
        store,
        base_node,
        prefix,
        model,
        n,
        max_tokens,
        temperature,
        strategy,
        rewind,
        show_strategy,
        show_usage,
        show_cost,
    )


@app.command("select")
def loom_select(
    node_id: Annotated[str, typer.Argument(help="Node id to mark active")],
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Mark a node as the active cursor."""
    store = GenerationStore(db)
    try:
        node = store.get(node_id)
    except AmbiguousNodeReference as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if node is None:
        console.print(f"[red]Unknown node: {node_id}[/red]")
        raise typer.Exit(1)
    store.set_active_node(node.id)
    console.print(f"[green]✓[/green] Active node set to {node.id}")


@app.command("nodes")
def loom_nodes(
    limit: Annotated[
        int, typer.Option("-n", "--limit", help="Number of recent nodes to show")
    ] = 20,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """List recently persisted generation nodes."""
    store = GenerationStore(db)
    rows = store.recent(limit)
    active_id = store.get_active_node_id()
    if not rows:
        console.print(f"[yellow]No nodes found in {store.db_path}.[/yellow]")
        return

    table = Table(
        "Active",
        "ID",
        "Name",
        "Parent",
        "Model",
        "Created",
        "Text",
        show_header=True,
        header_style="bold",
    )
    for node in rows:
        name = ""
        if node.parent_id is None and node.kind != "context":
            tree = store.tree_for_node(node.id)
            name = tree.name or ""
        table.add_row(
            "*" if node.id == active_id else "",
            node.id,
            name,
            node.parent_id or "",
            node.model or "",
            node.created_at,
            _preview(node.text),
        )
    console.print(table)


@app.command("active")
def loom_active(
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Show the currently active node."""
    store = GenerationStore(db)
    node = store.get_active_node()
    if node is None:
        console.print("[yellow]No active node stored yet.[/yellow]")
        return
    table = Table("Field", "Value", show_header=True, header_style="bold")
    table.add_row("ID", node.id)
    name = ""
    if node.parent_id is None and node.kind != "context":
        name = store.tree_for_node(node.id).name or ""
    table.add_row("Name", name)
    table.add_row("Parent", node.parent_id or "")
    table.add_row("Text", _preview(node.text, limit=120))
    console.print(table)


@app.command("show")
def loom_show(
    node_id: Annotated[str, typer.Argument(help="Node id to print")],
    segment: Annotated[
        bool, typer.Option("--segment", help="Print only this node's segment")
    ] = False,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Print a persisted node's full text."""
    store = GenerationStore(db)
    try:
        node = store.get(node_id)
    except AmbiguousNodeReference as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if node is None:
        console.print(f"[red]Unknown node: {node_id}[/red]")
        raise typer.Exit(1)
    console.print(node.text if segment else store.full_text(node_id))


@app.command("children")
def loom_children(
    node_id: Annotated[str, typer.Argument(help="Parent node id")],
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """List children of a persisted node."""
    store = GenerationStore(db)
    try:
        node = store.get(node_id)
    except AmbiguousNodeReference as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if node is None:
        console.print(f"[red]Unknown node: {node_id}[/red]")
        raise typer.Exit(1)
    rows = store.children(node.id)
    if not rows:
        console.print("[yellow]No children.[/yellow]")
        return
    table = Table(
        "ID",
        "Model",
        "Created",
        "Text",
        show_header=True,
        header_style="bold",
    )
    for node in rows:
        table.add_row(
            node.id,
            node.model or "",
            node.created_at,
            _preview(node.text),
        )
    console.print(table)


@app.command("roots")
def loom_roots(
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """List all root nodes (top-level generation trees)."""
    store = GenerationStore(db)
    rows = store.roots()
    active_id = store.get_active_node_id()
    if not rows:
        console.print(f"[yellow]No roots found in {store.db_path}.[/yellow]")
        return
    table = Table(
        "Active", "ID", "Name", "Children", "Created", "Text", header_style="bold"
    )
    for root in rows:
        child_count = len(store.children(root.id))
        tree = store.tree_for_node(root.id)
        table.add_row(
            "*" if root.id == active_id else "",
            root.id[:8],
            str(tree.name or ""),
            str(child_count),
            root.created_at,
            _preview(root.text),
        )
    console.print(table)


@app.command("embed")
def loom_embed(
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Embedder: 'hash', 'mlx', or an MLX/Hugging Face model ID",
        ),
    ] = "hash",
    dim: Annotated[
        int, typer.Option("--dim", help="Vector dimension for the hash embedder")
    ] = 256,
    min_chars: Annotated[
        int, typer.Option("--min-chars", help="Skip nodes shorter than this")
    ] = 1,
    batch_size: Annotated[
        int, typer.Option("--batch-size", help="Nodes per embedding batch")
    ] = 64,
    incremental: Annotated[
        bool,
        typer.Option(
            "--incremental",
            help="Embed new nodes and prune deleted nodes when model metadata matches",
        ),
    ] = False,
) -> None:
    """Build an in-database sqlite-vec index over node text."""
    from .retrieval.embedder import get_embedder
    from .retrieval.vectors import embed_corpus

    store = GenerationStore(db)
    try:
        embedder = get_embedder(model, dim=dim)
        embedded = embed_corpus(
            store.db_path,
            embedder,
            min_chars=min_chars,
            batch_size=batch_size,
            incremental=incremental,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    scope = "new node(s)" if incremental else "node(s)"
    console.print(
        f"[dim]Embedded {embedded:,} {scope} with {embedder.name} "
        f"(dim={embedder.dim}) into sqlite-vec ({store.db_path})[/dim]"
    )


@app.command("stats")
def loom_stats(
    node_id: Annotated[
        str | None,
        typer.Argument(help="Node id in the tree to analyze (defaults to active)"),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file", help="Analyze a loom JSON file instead of the SQLite store"
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON")
    ] = False,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Show quantitative stats for a loom tree."""
    from .stats import analyze_analysis_tree, analyze_tree

    if file is not None:
        from .loom_formats import load_loom_tree

        tree_data = load_loom_tree(file)
        stats = analyze_analysis_tree(tree_data, path_node_id=node_id)
        _print_loom_stats(stats, as_json=as_json)
        return

    store = GenerationStore(db)
    if node_id is not None:
        try:
            node = store.get(node_id)
        except AmbiguousNodeReference as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
    else:
        node = store.get_active_node()

    if node is None:
        console.print(
            "[red]No node found. Pass a node id or select an active node.[/red]"
        )
        raise typer.Exit(1)

    root = store.root(node.id)
    stats = analyze_tree(store, root.id, path_node_id=node.id)
    _print_loom_stats(stats, as_json=as_json)


@app.command("rating")
def loom_rating(
    root_id: Annotated[
        list[str] | None,
        typer.Option("--root", help="Restrict to these roots (repeatable)"),
    ] = None,
    signal: Annotated[
        str,
        typer.Option(
            "--signal",
            help="What counts as winning a batch: descendant, discounted, click, bookmark",
        ),
    ] = "descendant",
    models: Annotated[
        str | None,
        typer.Option(
            "--models",
            help="Comma-separated model names: rate one cohort only, which is the "
            "only way depth-conditional ratings are comparable across bands",
        ),
    ] = None,
    min_games: Annotated[
        int,
        typer.Option("--min-games", help="Hold out models with fewer comparisons"),
    ] = 20,
    resamples: Annotated[
        int, typer.Option("--resamples", help="Bootstrap resamples for the intervals")
    ] = 200,
    depth_bands: Annotated[
        int, typer.Option("--depth-bands", help="Depth bands to fit separately")
    ] = 3,
    cohort_size: Annotated[
        int, typer.Option("--cohort-size", help="Size of the recommended next line-up")
    ] = 4,
    raw_names: Annotated[
        bool,
        typer.Option("--raw-names", help="Rate gateway variants of a model separately"),
    ] = False,
    keep_indecisive: Annotated[
        bool,
        typer.Option("--keep-unjudged", help="Keep batches the user never judged"),
    ] = False,
    include_archived: Annotated[
        bool, typer.Option("--archived", help="Include archived trees")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON")
    ] = False,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Rate models against each other using the choices already in the trees.

    Every generation batch is a controlled comparison the user already made, so
    the corpus is a tournament. Unlike the per-model peer score in `stats`,
    these ratings share one scale across cohorts that never met.
    """
    from .rating import batches_from_store
    from .rating.batches import SIGNALS
    from .rating.report import analyze, render

    if signal not in SIGNALS:
        console.print(f"[red]--signal must be one of: {', '.join(SIGNALS)}[/red]")
        raise typer.Exit(1)

    store = GenerationStore(db)
    batches = batches_from_store(
        store, root_ids=root_id or None, include_archived=include_archived
    )
    if not batches:
        console.print("[red]No multi-completion batches found to rate.[/red]")
        raise typer.Exit(1)

    report = analyze(
        batches,
        signal=signal,
        merge_gateways=not raw_names,
        keep_indecisive=keep_indecisive,
        models=models.split(",") if models else None,
        min_games=min_games,
        resamples=resamples,
    )
    if not report.fit.ratings:
        console.print(
            "[red]No model has enough comparisons to rate. "
            "Try --min-games 0, or generate batches with more than one model.[/red]"
        )
        raise typer.Exit(1)
    if as_json:
        print(_json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        return
    print(
        render(
            report,
            signal=signal,
            depth_bands=depth_bands,
            cohort_size=cohort_size,
        )
    )


def _print_loom_stats(stats, *, as_json: bool) -> None:
    if as_json:
        print(_json.dumps(stats.as_dict(), indent=2, ensure_ascii=False))
        return

    tree = Table("Metric", "Value", show_header=False)
    tree.add_row("Root", stats.root_id)
    tree.add_row("Total nodes", str(stats.total_nodes))
    tree.add_row("Generated nodes", str(stats.generated_nodes))
    tree.add_row("Expanded nodes", str(stats.expanded_nodes))
    tree.add_row("Leaf nodes", str(stats.leaf_nodes))
    tree.add_row("Max depth", str(stats.max_depth))
    if stats.path is not None:
        tree.add_row("Path depth", str(stats.path.depth))
        tree.add_row("Path generated nodes", str(stats.path.generated_nodes))
    console.print(tree)

    models = Table(
        "Model",
        "Nodes",
        "Expanded",
        "Marked",
        "Hidden",
        "Expansion",
        "Mark %",
        "Hide %",
        "Mean NPDS",
        "Win %",
        "Mean DS",
        "Mean DDS",
        header_style="bold",
    )
    for model in stats.model_stats:
        models.add_row(
            model.model,
            str(model.nodes),
            str(model.expanded),
            str(model.bookmarked),
            str(model.hidden),
            _format_float(model.expansion_rate),
            _format_float(model.bookmark_rate),
            _format_float(model.hidden_rate),
            _format_float(model.normalized_peer_descendant_score.mean),
            _format_float(model.batch_win_rate.mean),
            _format_float(model.descendant_score.mean),
            _format_float(model.discounted_descendant_score.mean),
        )
    console.print(models)

    if stats.path and stats.path.models:
        path = Table("Path model", "Count", header_style="bold")
        for model, count in stats.path.models.items():
            path.add_row(model, str(count))
        console.print(path)


@app.command("publish-evidence")
def loom_publish_evidence(
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Include nodes at/after this ISO-8601 timestamp"),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Include nodes before this ISO-8601 timestamp"),
    ] = None,
    source_instance: Annotated[
        str | None,
        typer.Option(
            "--source-instance",
            help="Stable corpus label (the default is a private local hash)",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print aggregates without writing evidence"),
    ] = False,
) -> None:
    """Backfill private Loom usage as aggregate model evidence.

    No node IDs, tree IDs, prompts, or generated text leave the Loom database.
    """
    from .model_evidence import (
        collect_corpus_observations,
        observations_json,
        publish_corpus_statistics,
    )

    store = GenerationStore(db)
    if dry_run:
        observations = collect_corpus_observations(
            store, window_start=since, window_end=until
        )
        console.print(observations_json(observations))
        return
    try:
        count = publish_corpus_statistics(
            store,
            window_start=since,
            window_end=until,
            source_instance=source_instance,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[dim]Published {count:,} aggregate evidence row(s).[/dim]")


@app.command("view")
def loom_view(
    source: Annotated[
        str | None,
        typer.Argument(
            help="Node id, .txt file (use as root), or .json export to import"
        ),
    ] = None,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Interactive loom viewer. hjkl: nav. space: generate. q: quit."""
    if source is None and not sys.stdin.isatty():
        source = sys.stdin.read().rstrip("\n")
    from .config import load_config
    from .session import LoomSession
    from .tui.app import BasemodeApp

    store = GenerationStore(db)
    start = _resolve_loom_source(store, source)
    if start is None:
        return
    config = load_config()
    session = LoomSession(store, start.id)
    BasemodeApp(session, config).run()


def _resolve_loom_source(
    store: "GenerationStore", source: "str | None"
) -> "Node | None":
    """Resolve a source argument to a Node: None→active, file→import/create, str→node id."""
    if source is None:
        node = store.get_active_node()
        if node is None:
            console.print("[yellow]No active node.[/yellow]")
        return node

    p = Path(source)
    if p.suffix == ".json" and p.exists():
        return _import_loom_json(store, p)

    if p.exists() and p.is_file():
        text = p.read_text().rstrip("\n")
        existing = store.find_root_by_text(text)
        if existing:
            console.print(f"[dim]Found existing root {existing.id[:8]}[/dim]")
            return existing
        root = store.create_root(text, metadata={"source_file": str(p)})
        store.set_active_node(root.id)
        console.print(f"[dim]Created root {root.id[:8]} from {p.name}[/dim]")
        return root

    try:
        node = store.get(source)
    except AmbiguousNodeReference as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    if node is not None:
        return node

    # Treat as literal text: find existing root or create new one
    existing = store.find_root_by_text(source)
    if existing:
        console.print(f"[dim]Found existing root {existing.id[:8]}[/dim]")
        return existing
    root = store.create_root(source)
    store.set_active_node(root.id)
    console.print(f"[dim]Created root {root.id[:8]}[/dim]")
    return root


def _import_loom_json(store: "GenerationStore", path: Path) -> "Node | None":
    try:
        data = _json.loads(path.read_text())
    except Exception as exc:
        console.print(f"[red]Failed to read {path}: {exc}[/red]")
        return None
    raw_nodes = data.get("nodes", [])
    if not raw_nodes:
        console.print("[red]No nodes found in export.[/red]")
        return None
    from .store import Node as _Node

    nodes = [
        _Node(
            id=n["id"],
            parent_id=n.get("parent_id"),
            text=n["text"],
            model=n.get("model"),
            strategy=n.get("strategy"),
            max_tokens=n.get("max_tokens"),
            temperature=n.get("temperature"),
            created_at=n["created_at"],
            metadata=n.get("metadata", {}),
            tree_id=n.get("tree_id") or n.get("root_id") or n["id"],
            kind=n.get("kind", "text"),
            context_id=n.get("context_id"),
            checked_out=bool(n.get("checked_out", False)),
        )
        for n in raw_nodes
    ]
    inserted = store.import_nodes(nodes)
    skipped = len(nodes) - inserted
    console.print(f"[dim]Imported {inserted} nodes, skipped {skipped} duplicates[/dim]")
    root_node = next((n for n in nodes if n.parent_id is None), nodes[0])
    root = store.get(root_node.id)
    if root:
        store.set_active_node(root.id)
    return root


def _nodes_from_loom_json(path: Path) -> tuple[list[Node], dict[str, dict]]:
    data = _json.loads(path.read_text())
    raw_nodes = data.get("nodes", [])
    nodes = [
        Node(
            id=n["id"],
            parent_id=n.get("parent_id"),
            text=n["text"],
            model=n.get("model"),
            strategy=n.get("strategy"),
            max_tokens=n.get("max_tokens"),
            temperature=n.get("temperature"),
            created_at=n["created_at"],
            metadata=n.get("metadata", {}),
            tree_id=n.get("tree_id") or n.get("root_id") or n["id"],
            kind=n.get("kind", "text"),
            context_id=n.get("context_id"),
            checked_out=bool(n.get("checked_out", False)),
        )
        for n in raw_nodes
    ]
    # A JSON export carries no tree row; the root node's own metadata is what
    # `import_nodes` reads its settings back out of.
    return nodes, {}


def _nodes_from_sqlite(path: Path) -> tuple[list[Node], dict[str, dict]]:
    """Read a source database without opening (or migrating) it as a store."""
    import sqlite3

    uri = f"file:{path}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM nodes").fetchall()
        trees = {
            str(row["id"]): {
                "name": row["name"],
                "show_model_names": bool(row["show_model_names"]),
                "rewind_split_tokens": row["rewind_split_tokens"],
                "global_max_tokens": row["global_max_tokens"],
                "global_n_branches": row["global_n_branches"],
                "model_plan": _json.loads(row["model_plan_json"]),
            }
            for row in conn.execute("SELECT * FROM trees").fetchall()
        }
    nodes = [
        Node(
            id=row["id"],
            parent_id=row["parent_id"],
            text=row["text"],
            model=row["model"],
            strategy=row["strategy"],
            max_tokens=row["max_tokens"],
            temperature=row["temperature"],
            created_at=row["created_at"],
            metadata=_json.loads(row["metadata_json"]),
            tree_id=row["tree_id"] or row["id"],
            kind=row["kind"],
            context_id=row["context_id"],
            checked_out=bool(row["checked_out"]),
        )
        for row in rows
    ]
    return nodes, trees


def _read_import_source(path: Path) -> tuple[list[Node], dict[str, dict]]:
    if path.suffix.lower() == ".json":
        return _nodes_from_loom_json(path)
    with path.open("rb") as handle:
        if handle.read(16).startswith(b"SQLite format 3"):
            return _nodes_from_sqlite(path)
    raise ValueError(f"{path} is neither a loom JSON export nor a SQLite database")


@app.command("import")
def loom_import(
    source: Annotated[
        Path,
        typer.Argument(
            help="A loom JSON export, or another SQLite generation database"
        ),
    ],
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite generation database to import into"),
    ] = None,
    tree: Annotated[
        list[str] | None,
        typer.Option("--tree", help="Only import these tree ids (repeatable)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Report what would be imported, and write nothing"
        ),
    ] = False,
    keep_checked_out: Annotated[
        bool,
        typer.Option(
            "--keep-checked-out",
            help="Keep the source's checked-out flags even when adding to a tree that already exists here",
        ),
    ] = False,
    update: Annotated[
        bool,
        typer.Option(
            "--update",
            help="Also overwrite the content of nodes already here when it differs at the source",
        ),
    ] = False,
) -> None:
    """Add trees and nodes from another database or export.

    Nodes already present are left exactly as they are unless `--update` is
    given, in which case their content is overwritten from the source. Without
    it an import only ever adds.
    Ids are uuids, so a tree carries its identity between machines and an
    import can be repeated without duplicating anything.

    `--update` rewrites a node's text, model, settings and metadata; it never
    moves a node in the tree and never touches where either side was reading.
    There is no per-node modified time to arbitrate with, so the source simply
    wins: check `--dry-run` first.

    A node joining a tree that already exists here arrives with its
    checked-out flag cleared, so an import cannot move where this database
    was last reading. Nodes belonging to a tree that is new here keep theirs,
    which is what puts a freshly imported tree on its own checked-out path.
    """
    if not source.exists():
        console.print(f"[red]No such file: {source}[/red]")
        raise typer.Exit(1)
    try:
        nodes, source_trees = _read_import_source(source)
    except Exception as exc:
        console.print(f"[red]Could not read {source}: {exc}[/red]")
        raise typer.Exit(1) from None
    if tree:
        wanted = set(tree)
        nodes = [n for n in nodes if n.tree_id in wanted]
    if not nodes:
        console.print("[yellow]Nothing to import.[/yellow]")
        return

    store = GenerationStore(db)
    present = store.existing_node_ids([n.id for n in nodes])
    incoming = [n for n in nodes if n.id not in present]
    outdated = store.diff_nodes([n for n in nodes if n.id in present]) if update else []
    known_trees = set(store.tree_index())
    # Naming considers every tree in the source, not just the ones with new
    # nodes: a tree imported before names were carried across has nothing new
    # to add and still wants its name.
    trees_in_scope = {n.tree_id for n in nodes}
    if not incoming and not outdated:
        named = _carry_tree_names(
            store, trees_in_scope, known_trees, source_trees, dry_run=dry_run
        )
        console.print(
            f"[dim]Nothing to do: all {len(nodes)} nodes are already here"
            + ("" if update else ", and --update was not given")
            + ".[/dim]"
            + (f" [green]Named {named} trees.[/green]" if named else "")
        )
        return

    if not keep_checked_out:
        incoming = [
            dataclasses.replace(n, checked_out=False) if n.tree_id in known_trees else n
            for n in incoming
        ]

    by_tree: dict[str, int] = {}
    for node in incoming:
        by_tree[node.tree_id] = by_tree.get(node.tree_id, 0) + 1
    if by_tree:
        table = Table(box=None, pad_edge=False)
        table.add_column("Tree")
        table.add_column("Name")
        table.add_column("Nodes", justify="right")
        for tree_id, count in sorted(by_tree.items(), key=lambda kv: -kv[1]):
            table.add_row(
                tree_id[:8],
                (source_trees.get(tree_id) or {}).get("name") or "[dim]—[/dim]",
                f"{count} new" if tree_id in known_trees else f"{count} (new tree)",
            )
        console.print(table)

    new_trees = sum(1 for tree_id in by_tree if tree_id not in known_trees)
    work = []
    if incoming:
        work.append(
            f"add {len(incoming)} nodes across {len(by_tree)} trees ({new_trees} new)"
        )
    if outdated:
        work.append(f"update {len(outdated)} nodes already here")
    summary = " and ".join(work) + f"; {len(nodes) - len(incoming)} already present"
    if dry_run:
        named = _carry_tree_names(
            store, trees_in_scope, known_trees, source_trees, dry_run=True
        )
        if named:
            summary += f", naming {named} trees"
        console.print(f"[yellow]Would {summary}.[/yellow]")
        return

    inserted = store.import_nodes(incoming)
    updated = store.update_nodes(outdated)
    named = _carry_tree_names(store, trees_in_scope, known_trees, source_trees)
    console.print(
        f"[green]Imported {inserted} nodes"
        + (f", updated {updated}" if updated else "")
        + f".[/green] {len(nodes) - len(incoming)} already present."
    )
    if named:
        console.print(f"[dim]Named {named} trees from the source.[/dim]")


def _carry_tree_names(
    store: GenerationStore,
    tree_ids: set[str],
    known_trees: set[str],
    source_trees: dict[str, dict],
    *,
    dry_run: bool = False,
) -> int:
    """Give an imported tree the name it had at the source.

    A tree's name and generation settings live on its `trees` row, not on its
    nodes, so importing nodes alone leaves a new tree nameless. Only a tree
    with no name here is touched, so this never renames a tree that has one
    and can be re-run over an import that predates it.
    """
    named = 0
    for tree_id in sorted(tree_ids):
        settings = source_trees.get(tree_id)
        if not settings or not settings.get("name"):
            continue
        tree = store.get_tree(tree_id)
        if tree is None or tree.name:
            continue
        fields: dict[str, object] = {"name": settings["name"]}
        if tree_id not in known_trees:
            # New here, so its generation settings are this import's to set.
            fields.update(
                show_model_names=settings["show_model_names"],
                rewind_split_tokens=settings["rewind_split_tokens"],
                global_max_tokens=settings["global_max_tokens"],
                global_n_branches=settings["global_n_branches"],
                model_plan=settings["model_plan"],
            )
        if not dry_run:
            store.update_tree_settings(tree_id, **fields)  # type: ignore[arg-type]
        named += 1
    return named


@app.command("export")
def loom_export(
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="Output file path, 'json' for JSON stdout, or 'md' for Markdown stdout",
        ),
    ] = "json",
    node_id: Annotated[
        str | None,
        typer.Option("--node", help="Any node in the tree (defaults to active)"),
    ] = None,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
) -> None:
    """Export a loom tree as JSON or the checked-out path as Markdown."""
    store = GenerationStore(db)
    if node_id is not None:
        try:
            node = store.get(node_id)
        except AmbiguousNodeReference as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        if node is None:
            console.print(f"[red]Unknown node: {node_id}[/red]")
            raise typer.Exit(1)
    else:
        node = store.get_active_node()
        if node is None:
            console.print("[red]No active node. Use --node to specify one.[/red]")
            raise typer.Exit(1)

    root = store.root(node.id)
    tree_nodes = store.tree(root.id)

    if _export_format(to) == "md":
        checked_out = _checked_out_node(store, root, node)
        serialized = store.full_text(checked_out.id)
        if not serialized.endswith("\n"):
            serialized += "\n"
    else:
        serialized = _serialize_loom_json(tree_nodes)

    if to in {"json", "md", "markdown"}:
        print(serialized, end="" if serialized.endswith("\n") else "\n")
    else:
        out = Path(to)
        out.write_text(serialized, encoding="utf-8")
        if _export_format(to) == "md":
            console.print(f"[dim]Exported checked-out path \u2192 {out}[/dim]")
        else:
            console.print(f"[dim]Exported {len(tree_nodes)} nodes \u2192 {out}[/dim]")


def _export_format(to: str) -> str:
    if to in {"md", "markdown"}:
        return "md"
    if Path(to).suffix.lower() in {".md", ".markdown"}:
        return "md"
    return "json"


def _checked_out_node(store: GenerationStore, root: Node, fallback: Node) -> Node:
    tree = store.tree_for_node(root.id)
    last_id = tree.current_node_id
    if isinstance(last_id, str):
        last = store.get(last_id)
        if last is not None and last.tree_id == root.tree_id and last.id != root.id:
            return last

    node = root
    while True:
        checked_id = store.get_checked_out_child_id(node.id)
        children = store.children(node.id)
        checked = next((child for child in children if child.id == checked_id), None)
        if checked is None:
            break
        node = checked

    if node.id != root.id:
        return node
    return fallback


def _serialize_loom_json(tree_nodes: list[Node]) -> str:
    data = {
        "version": 1,
        "nodes": [
            {
                "id": n.id,
                "parent_id": n.parent_id,
                "tree_id": n.tree_id,
                "kind": n.kind,
                "text": n.text,
                "context_id": n.context_id,
                "model": n.model,
                "strategy": n.strategy,
                "max_tokens": n.max_tokens,
                "temperature": n.temperature,
                "checked_out": n.checked_out,
                "created_at": n.created_at,
                "metadata": n.metadata,
            }
            for n in tree_nodes
        ],
    }
    return _json.dumps(data, indent=2, ensure_ascii=False)


def _resolve_loom_base(
    store: GenerationStore, active: Node, branch: int | None
) -> Node:
    children = store.children(active.id)
    if branch is not None:
        try:
            return store.select_branch(active.id, branch)
        except (IndexError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

    if len(children) == 1:
        return children[0]
    if len(children) > 1:
        console.print(
            f"[red]Active node {active.id} has {len(children)} branches. Use -b N to select one.[/red]"
        )
        raise typer.Exit(1)
    return active


def _run_loom_generation(
    store: GenerationStore,
    base_node: Node | None,
    prefix: str,
    model: str | None,
    n: int,
    max_tokens: int,
    temperature: float,
    strategy: str | None,
    rewind: bool,
    show_strategy: bool,
    show_usage: bool,
    show_cost: bool,
) -> None:
    if model is None:
        model = get_default_model() or "gpt-4o-mini"
    prefix = prefix.rstrip("\n")
    if show_strategy:
        from basemode.detect import detect_strategy

        strat = detect_strategy(resolve_model_id(model), strategy)
        console.print(f"[dim]strategy: {strat.name}[/dim]")
    if n == 1:
        completion = asyncio.run(
            _stream_one(prefix, model, max_tokens, temperature, strategy, rewind)
        )
        _save_loom_run(
            store,
            prefix,
            [completion],
            model,
            strategy,
            max_tokens,
            temperature,
            base_node.id if base_node is not None else None,
        )
        if show_usage or show_cost:
            _print_usage_estimate(
                model, prefix, completion, strategy, show_cost, prompt_requests=1
            )
    else:
        completions = asyncio.run(
            _stream_branches(
                prefix, model, n, max_tokens, temperature, strategy, rewind
            )
        )
        _save_loom_run(
            store,
            prefix,
            completions,
            model,
            strategy,
            max_tokens,
            temperature,
            base_node.id if base_node is not None else None,
        )
        if show_usage or show_cost:
            _print_usage_estimate(
                model,
                prefix,
                "".join(completions),
                strategy,
                show_cost,
                prompt_requests=n,
            )


def _save_loom_run(
    store: GenerationStore,
    prefix: str,
    completions: list[str],
    model: str | None,
    strategy: str | None,
    max_tokens: int,
    temperature: float,
    active_node_id: str | None,
) -> None:
    from basemode.detect import detect_strategy
    from basemode.healing import normalize_completion_segment

    resolved = resolve_model_id(model or get_default_model() or "gpt-4o-mini")
    strategy_name = detect_strategy(resolved, strategy).name
    completions = [
        normalize_completion_segment(prefix, completion) for completion in completions
    ]
    parent, children = store.save_continuations(
        prefix,
        completions,
        model=resolved,
        strategy=strategy_name,
        max_tokens=max_tokens,
        temperature=temperature,
        parent_id=active_node_id,
    )
    console.print(f"[dim]saved parent: {parent.id}[/dim]")
    for child in children:
        console.print(f"[dim]saved child: {child.id}[/dim]")
    base_id = active_node_id or parent.id
    store.set_active_node(base_id if len(children) > 1 else children[0].id)
    _maybe_name_tree(store, children)


def _maybe_name_tree(store: GenerationStore, children: list[Node]) -> None:
    if not children:
        return
    root = store.root(children[0].id)
    tree = store.tree_for_node(root.id)
    if tree.name:
        return

    candidates = [(child, store.full_text(child.id)) for child in children]
    child, text = max(candidates, key=lambda item: len(item[1]))
    if not should_name(text):
        return

    name = generate_name(text)
    if name is None:
        return
    store.update_tree_settings(
        root.tree_id, name=name, metadata={"named_from": child.id}
    )
    console.print(f"[dim]named tree: {name}[/dim]")


def _print_usage_estimate(
    model: str,
    prefix: str,
    completion: str,
    strategy: str | None,
    show_cost: bool,
    prompt_requests: int,
) -> None:
    from basemode.usage import estimate_usage, format_usd

    resolved = resolve_model_id(model)
    prompt, messages = _usage_prompt(resolved, prefix, strategy)
    usage = estimate_usage(
        resolved,
        prompt,
        completion,
        prompt_messages=messages,
        prompt_requests=prompt_requests,
    )
    table = Table("Metric", "Value", show_header=False)
    table.add_row("Model", usage.model)
    table.add_row("Prompt tokens", str(usage.prompt_tokens))
    table.add_row("Completion tokens", str(usage.completion_tokens))
    table.add_row("Total tokens", str(usage.total_tokens))
    if show_cost:
        table.add_row("Estimated cost", format_usd(usage.cost_usd))
        if not usage.pricing_available:
            table.add_row("Cost note", "pricing unavailable in LiteLLM model map")
    console.print(table)


def _usage_prompt(
    model: str, prefix: str, strategy: str | None
) -> tuple[str, list[dict] | None]:
    from basemode.detect import detect_strategy
    from basemode.healing import normalize_prefix
    from basemode.strategies.few_shot import _SYSTEM_PROMPT as FEW_SHOT_SYSTEM_PROMPT
    from basemode.strategies.fim import _fim_prompt
    from basemode.strategies.prefill import SEED_LEN
    from basemode.strategies.system import SYSTEM_PROMPT

    strat = detect_strategy(model, strategy)
    if strat.name == "system":
        return "", [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strat.name == "few_shot":
        return "", [
            {"role": "system", "content": FEW_SHOT_SYSTEM_PROMPT},
            {"role": "user", "content": normalize_prefix(prefix)},
        ]
    if strat.name == "prefill":
        seed = prefix[-SEED_LEN:] if len(prefix) > SEED_LEN else prefix
        return "", [
            {
                "role": "system",
                "content": (
                    "You are continuing the following text. "
                    "Output only the continuation — no preamble, no commentary.\n\n"
                    f"Text to continue:\n{prefix}"
                ),
            },
            {"role": "user", "content": "[continue]"},
            {"role": "assistant", "content": seed},
        ]
    if strat.name == "fim":
        return _fim_prompt(prefix, model), None
    return prefix, None


@app.command("serve")
def loom_serve(
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8000,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite generation database path")
    ] = None,
    production: Annotated[
        bool, typer.Option("--production", help="Enable fail-closed production mode")
    ] = False,
    public: Annotated[
        bool, typer.Option("--public", help="Allow binding to a non-loopback address")
    ] = False,
    enable_docs: Annotated[
        bool, typer.Option("--enable-docs", help="Enable API docs in production")
    ] = False,
) -> None:
    """Start the basemode-loom web API server."""
    import uvicorn

    from .api import create_app
    from .config import load_config

    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback and not public:
        raise typer.BadParameter(
            "non-loopback binds require the explicit --public flag", param_hint="--host"
        )

    store = GenerationStore(db)
    config = load_config()
    server = config.server
    if production:
        server = dataclasses.replace(server, production=True)
    if enable_docs:
        server = dataclasses.replace(server, enable_docs=True)
    config = dataclasses.replace(config, server=server)
    web_app = create_app(store, config)
    console.print(f"[dim]basemode-loom API → http://{host}:{port}[/dim]")
    uvicorn.run(web_app, host=host, port=port)


def _preview(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_float(value: float) -> str:
    return f"{value:.2f}"
