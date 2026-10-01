from __future__ import annotations

import basemode
import pytest
from typer.testing import CliRunner

from basemode_loom import chat
from basemode_loom.cli import app
from basemode_loom.store import GenerationStore

runner = CliRunner()


@pytest.fixture
def fake_chat(monkeypatch):
    """Replace basemode's chat_text; replies are scripted per model."""
    calls: list[dict] = []
    scripts: dict[str, list[str] | Exception] = {}

    async def fake_chat_text(messages, model, **kwargs):
        calls.append({"messages": messages, "model": model, **kwargs})
        script = scripts.get(model, ["ok"])
        if isinstance(script, Exception):
            raise script
        for token in script:
            yield token

    monkeypatch.setattr(basemode, "chat_text", fake_chat_text, raising=False)
    return calls, scripts


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    path = tmp_path / "chats.sqlite"
    monkeypatch.setenv("BASEMODE_CHAT_DB", str(path))
    monkeypatch.setenv("BASEMODE_DB", str(tmp_path / "generations.sqlite"))
    return path


def test_start_chat_flags_the_tree_and_keeps_the_system_prompt(store) -> None:
    root = chat.start_chat(store, "hello there", system="be terse")

    assert chat.is_chat_tree(store, root.id)
    assert chat.node_role(root) == "user"
    assert store.tree_for_node(root.id).name == "hello there"
    assert chat.chat_messages(store, root.id) == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello there"},
    ]


def test_ordinary_loom_trees_are_not_chats(store) -> None:
    root = store.create_root("Once upon a time")

    assert not chat.is_chat_tree(store, root.id)
    assert chat.latest_chat_node(store) is None


def test_chat_messages_merge_consecutive_same_role_nodes(store) -> None:
    root = chat.start_chat(store, "part one, ")
    second = chat.add_user_turn(store, root.id, "part two")

    assert chat.chat_messages(store, second.id) == [
        {"role": "user", "content": "part one, part two"}
    ]


@pytest.mark.asyncio
async def test_reply_saves_assistant_branches_and_checks_out_the_first(
    store, fake_chat
) -> None:
    calls, scripts = fake_chat
    scripts["anthropic/a"] = ["Hi", " there"]
    scripts["openai/b"] = ["Yo"]
    root = chat.start_chat(store, "hello")
    seen: list[tuple[int, str]] = []

    saved, failures = await chat.reply(
        store,
        root.id,
        ["anthropic/a", "openai/b"],
        on_token=lambda slot, token: seen.append((slot, token)),
    )

    assert failures == []
    assert [node.text for node in saved] == ["Hi there", "Yo"]
    assert all(chat.node_role(node) == "assistant" for node in saved)
    assert all(node.strategy == "chat" for node in saved)
    assert {slot for slot, _ in seen} == {0, 1}
    assert calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert calls[0]["observation"].source == "loom"
    assert chat.latest_chat_node(store).id == saved[0].id
    assert store.get_checked_out_child_id(root.id) == saved[0].id


@pytest.mark.asyncio
async def test_reply_reports_failed_and_empty_branches(store, fake_chat) -> None:
    _calls, scripts = fake_chat
    scripts["bad/model"] = RuntimeError("boom")
    scripts["empty/model"] = ["  "]
    root = chat.start_chat(store, "hello")

    saved, failures = await chat.reply(
        store, root.id, ["bad/model", "empty/model", "good/model"]
    )

    assert [node.text for node in saved] == ["ok"]
    assert [(f.model, str(f.error)) for f in failures] == [
        ("bad/model", "boom"),
        ("empty/model", "empty reply"),
    ]


def test_cli_chat_round_trip(chat_db, fake_chat) -> None:
    calls, scripts = fake_chat
    scripts["anthropic/a"] = ["Islay"]

    first = runner.invoke(
        app, ["chat", "pick an island", "-m", "anthropic/a", "-s", "be terse"]
    )
    assert first.exit_code == 0, first.output
    assert "Islay" in first.output

    scripts["anthropic/a"] = ["About 3,200."]
    second = runner.invoke(app, ["chat", "-c", "population?", "-m", "anthropic/a"])
    assert second.exit_code == 0, second.output
    assert calls[-1]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "pick an island"},
        {"role": "assistant", "content": "Islay"},
        {"role": "user", "content": "population?"},
    ]

    shown = runner.invoke(app, ["chat", "-c"])
    assert shown.exit_code == 0
    assert "About 3,200." in shown.output


def test_cli_chat_prepends_piped_stdin(chat_db, fake_chat) -> None:
    calls, _scripts = fake_chat

    result = runner.invoke(
        app, ["chat", "summarise", "-m", "x/y"], input="some notes\n"
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["messages"] == [
        {"role": "user", "content": "some notes\n\nsummarise"}
    ]


def test_cli_chat_branch_switch_continues_from_the_chosen_reply(
    chat_db, fake_chat
) -> None:
    calls, scripts = fake_chat
    scripts["m/one"] = ["first"]
    scripts["m/two"] = ["second"]
    assert (
        runner.invoke(app, ["chat", "hi", "-m", "m/one", "-m", "m/two"]).exit_code == 0
    )

    result = runner.invoke(app, ["chat", "-c", "-b", "2", "go on", "-m", "m/one"])

    assert result.exit_code == 0, result.output
    assert {"role": "assistant", "content": "second"} in calls[-1]["messages"]


def test_cli_chat_failed_send_leaves_no_trace(chat_db, fake_chat) -> None:
    _calls, scripts = fake_chat
    scripts["bad/model"] = RuntimeError("nope")
    assert runner.invoke(app, ["chat", "hi", "-m", "good/model"]).exit_code == 0

    failed = runner.invoke(app, ["chat", "-c", "lost", "-m", "bad/model"])

    assert failed.exit_code == 1
    assert "nope" in failed.output
    current = chat.latest_chat_node(GenerationStore(chat_db))
    assert current.text == "ok"
    assert store_children_texts(chat_db, current.id) == []


def test_cli_chat_never_touches_the_loom_database(chat_db, fake_chat, tmp_path) -> None:
    assert runner.invoke(app, ["chat", "hi", "-m", "x/y"]).exit_code == 0

    loom = GenerationStore(tmp_path / "generations.sqlite")
    assert loom.roots() == []
    assert loom.get_active_node_id() is None


def store_children_texts(db, node_id) -> list[str]:
    return [node.text for node in GenerationStore(db).children(node_id)]


def test_list_chats_summarises_turns_replies_and_models(store, fake_chat) -> None:
    import asyncio

    older = chat.start_chat(store, "first chat")
    asyncio.run(chat.reply(store, older.id, ["m/one", "m/two"]))
    store.create_root("an ordinary loom tree")
    newer = chat.start_chat(store, "second chat")

    chats = chat.list_chats(store)

    assert [c.name for c in chats] == ["second chat", "first chat"]
    assert chats[0].current.id == newer.id
    assert (chats[1].turns, chats[1].replies, chats[1].models) == (
        2,
        2,
        ("m/one", "m/two"),
    )


def test_resolve_chat_accepts_tree_prefixes_and_rejects_loom_trees(store) -> None:
    root = chat.start_chat(store, "hello")
    loom_root = store.create_root("not a chat")

    assert chat.resolve_chat(store, root.id[:6]).id == root.id
    assert chat.resolve_chat(store, loom_root.id) is None
    assert chat.resolve_chat(store, "ffffffffffff") is None


def test_cli_chat_resume_continues_an_older_chat(chat_db, fake_chat) -> None:
    calls, _scripts = fake_chat
    assert runner.invoke(app, ["chat", "older chat", "-m", "x/y"]).exit_code == 0
    older_id = chat.list_chats(GenerationStore(chat_db))[0].tree_id
    assert runner.invoke(app, ["chat", "newer chat", "-m", "x/y"]).exit_code == 0

    resumed = runner.invoke(app, ["chat", "-r", older_id[:8], "more", "-m", "x/y"])
    assert resumed.exit_code == 0, resumed.output
    assert calls[-1]["messages"][0] == {"role": "user", "content": "older chat"}

    # Resuming made it the latest, so plain -c follows on from it.
    assert runner.invoke(app, ["chat", "-c", "again", "-m", "x/y"]).exit_code == 0
    assert calls[-1]["messages"][0] == {"role": "user", "content": "older chat"}

    listed = runner.invoke(app, ["chat", "--list"])
    assert listed.exit_code == 0
    assert older_id[:8] in listed.output


def test_cli_chat_resume_reports_an_unknown_chat(chat_db) -> None:
    result = runner.invoke(app, ["chat", "-r", "deadbeef"])

    assert result.exit_code == 1
    assert "No chat matches" in result.output
