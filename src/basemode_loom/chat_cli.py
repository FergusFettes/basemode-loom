"""`basemode-loom chat`: a plain chat stored as a loom tree."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from typing import Annotated

import typer
from basemode.keys import get_default_model
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.text import Text

from . import chat
from .cli import _BRANCH_COLORS, app, console
from .store import GenerationStore, Node

_err = Console(stderr=True)


@app.command("chat")
def loom_chat(
    ctx: typer.Context,
    message: Annotated[
        str | None,
        typer.Argument(help="Your message; piped stdin is prepended to it"),
    ] = None,
    cont: Annotated[
        bool,
        typer.Option(
            "-c",
            "--continue",
            help="Continue the most recent chat instead of a new one",
        ),
    ] = False,
    branch: Annotated[
        int | None,
        typer.Option(
            "-b",
            "--branch",
            min=1,
            help="With -c: switch to sibling reply N of the last turn first",
        ),
    ] = None,
    models: Annotated[
        list[str] | None,
        typer.Option("-m", "--model", help="Model to answer; repeat to ask several"),
    ] = None,
    n: Annotated[
        int, typer.Option("-n", "--branches", min=1, help="Replies per model")
    ] = 1,
    system: Annotated[
        str | None,
        typer.Option("-s", "--system", help="System prompt (new chats only)"),
    ] = None,
    max_tokens: Annotated[int, typer.Option("-M", "--max-tokens")] = 4096,
    temperature: Annotated[float, typer.Option("-t", "--temperature")] = 0.7,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Chat database (default: chats.sqlite)"),
    ] = None,
) -> None:
    """Chat with a model; every turn is a node, every reply a branch.

    `chat "hi"` starts a new chat, `chat -c "and then?"` continues the latest
    one, and `chat -c` alone prints it. Chats are kept apart from loom's own
    trees (see --db).
    """
    text = "\n\n".join(p for p in (_read_piped_stdin().strip(), message) if p)
    if not text and not cont:
        console.print(ctx.get_help())
        return
    if system and cont:
        _err.print("[red]--system only applies when starting a new chat[/red]")
        raise typer.Exit(2)

    store = GenerationStore(db or chat.default_chat_db_path())
    if cont:
        current = chat.latest_chat_node(store)
        if current is None:
            _err.print("[red]No chat to continue yet.[/red]")
            raise typer.Exit(1)
        if branch is not None:
            current = _switch_branch(store, current, branch)
        if not text:
            _print_transcript(store, current)
            return
        user_turn = chat.add_user_turn(store, current.id, text)
    else:
        user_turn = chat.start_chat(store, text, system=system)

    model_list = models or [str(get_default_model() or "gpt-4o-mini")]
    saved, failures = asyncio.run(
        _stream_replies(store, user_turn, model_list, n, max_tokens, temperature)
    )
    for failure in failures:
        detail = str(failure.error).strip().splitlines()
        _err.print(
            f"[red]error[/red] {failure.model}: {type(failure.error).__name__}: "
            f"{detail[-1] if detail else ''}",
            markup=True,
            highlight=False,
        )
    if not saved:
        # A send nobody answered leaves no trace, so the next `-c` carries on
        # from the last real reply instead of gluing onto a dead turn.
        chat.discard_turn(store, user_turn)
        raise typer.Exit(1)
    if len(saved) > 1:
        _err.print(
            f"[dim]{len(saved)} replies saved; continuing from branch 1 "
            "(pick another with `chat -c -b N`).[/dim]"
        )


async def _stream_replies(
    store: GenerationStore,
    user_turn: Node,
    models: list[str],
    n: int,
    max_tokens: int,
    temperature: float,
) -> tuple[list[Node], list[chat.ReplyFailure]]:
    slots = len(models) * n
    if slots == 1:
        saved, failures = await chat.reply(
            store,
            user_turn.id,
            models,
            n=n,
            max_tokens=max_tokens,
            temperature=temperature,
            on_token=lambda _slot, token: _write(token),
        )
        if saved and not saved[0].text.endswith("\n"):
            _write("\n")
        return saved, failures

    labels = [m for m in models for _ in range(n)]
    buffers: list[list[str]] = [[] for _ in range(slots)]

    def panel() -> Columns:
        columns = []
        for i, buf in enumerate(buffers):
            color = _BRANCH_COLORS[i % len(_BRANCH_COLORS)]
            body = Text(f"{i + 1}. {labels[i]}\n", style=f"bold {color}")
            body.append("".join(buf))
            columns.append(body)
        return Columns(columns, equal=True, expand=True)

    with Live(panel(), console=console, refresh_per_second=12) as live:

        def on_token(slot: int, token: str) -> None:
            buffers[slot].append(token)
            live.update(panel())

        return await chat.reply(
            store,
            user_turn.id,
            models,
            n=n,
            max_tokens=max_tokens,
            temperature=temperature,
            on_token=on_token,
        )


def _switch_branch(store: GenerationStore, current: Node, branch: int) -> Node:
    if current.parent_id is None:
        _err.print("[red]The chat has no replies to choose between yet.[/red]")
        raise typer.Exit(1)
    siblings = store.children(current.parent_id)
    if branch > len(siblings):
        _err.print(f"[red]Only {len(siblings)} replies to choose from.[/red]")
        raise typer.Exit(1)
    chosen = siblings[branch - 1]
    store.set_checked_out_child(current.parent_id, chosen.id)
    store.set_active_node(chosen.id)
    return chosen


def _print_transcript(store: GenerationStore, node: Node) -> None:
    for message in chat.chat_messages(store, node.id):
        console.rule(f"[bold]{message['role']}[/bold]", align="left", style="dim")
        console.print(message["content"], markup=False, highlight=False)


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _read_piped_stdin() -> str:
    """Read stdin only when something was actually piped or redirected in.

    A non-tty character device that never reaches EOF (agents, cron, editors)
    would otherwise hang the command.
    """
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (AttributeError, OSError, ValueError):
        return "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    if stat.S_ISFIFO(mode) or stat.S_ISREG(mode):
        return sys.stdin.read()
    return ""
