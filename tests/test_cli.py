from click import unstyle
from typer.testing import CliRunner

from basemode_loom.cli import app
from basemode_loom.store import GenerationStore

runner = CliRunner()


def test_top_level_help_includes_loom() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "loom" in result.output


def test_loom_help_lists_stateful_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "continue" in result.output
    assert "active" in result.output
    assert "nodes" in result.output


def test_serve_requires_public_acknowledgement_for_non_loopback_bind() -> None:
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    output = unstyle(result.output)
    assert "non-loopback binds require" in output
    assert "--public" in output


def test_loom_continue_and_branch_selection(tmp_path, monkeypatch) -> None:
    db = tmp_path / "generations.sqlite"

    async def fake_stream_one(*args, **kwargs):
        return " gamma"

    monkeypatch.setattr("basemode_loom.cli._stream_one", fake_stream_one)
    monkeypatch.setattr("basemode_loom.cli.generate_name", lambda text: None)
    monkeypatch.setattr("basemode_loom.cli.should_name", lambda text: False)

    store = GenerationStore(db)
    parent, _ = store.save_continuations(
        "Seed",
        [" alpha", " beta"],
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.set_active_node(parent.id)

    second = runner.invoke(app, ["continue", "-b", "2", "--db", str(db)])
    assert second.exit_code == 0, second.output

    store = GenerationStore(db)
    active = store.get_active_node()
    assert active is not None
    assert store.full_text(active.id).endswith("beta gamma")


def test_loom_select_marks_active(tmp_path) -> None:
    db = tmp_path / "generations.sqlite"

    store = GenerationStore(db)
    parent, children = store.save_continuations(
        "Seed",
        [" alpha", " beta"],
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.set_active_node(parent.id)
    child = children[0]

    select = runner.invoke(app, ["select", child.id[:10], "--db", str(db)])
    assert select.exit_code == 0, select.output

    nodes = runner.invoke(app, ["nodes", "--db", str(db)])
    assert nodes.exit_code == 0, nodes.output
    assert "*" in nodes.output

    active_output = runner.invoke(app, ["active", "--db", str(db)])
    assert active_output.exit_code == 0, active_output.output
    assert child.id in active_output.output

    show = runner.invoke(app, ["show", child.id[:10], "--segment", "--db", str(db)])
    assert show.exit_code == 0, show.output
    assert " alpha" in show.output

    children = runner.invoke(app, ["children", parent.id[:10], "--db", str(db)])
    assert children.exit_code == 0, children.output
    assert "alpha" in children.output
    assert "beta" in children.output

    active = GenerationStore(db).get_active_node()
    assert active is not None
    assert active.id == child.id


def test_loom_export_md_prints_checked_out_path(tmp_path) -> None:
    db = tmp_path / "generations.sqlite"
    store = GenerationStore(db)
    parent, children = store.save_continuations(
        "Seed",
        [" alpha", " beta"],
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    grandchild = store.add_child(
        children[1].id,
        " gamma",
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.set_checked_out_child(parent.id, children[1].id)
    store.set_checked_out_child(children[1].id, grandchild.id)
    store.set_active_node(parent.id)

    result = runner.invoke(app, ["export", "--to", "md", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.output == "Seed beta gamma\n"


def test_loom_export_md_file_uses_extension(tmp_path) -> None:
    db = tmp_path / "generations.sqlite"
    out = tmp_path / "checked-out.md"
    store = GenerationStore(db)
    _parent, children = store.save_continuations(
        "Seed",
        [" alpha"],
        model="gpt-4o-mini",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.set_active_node(children[0].id)

    result = runner.invoke(app, ["export", "--to", str(out), "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert out.read_text() == "Seed alpha\n"


def test_loom_stats_prints_tree_and_model_stats(tmp_path) -> None:
    db = tmp_path / "generations.sqlite"
    store = GenerationStore(db)
    parent, children = store.save_continuations(
        "Seed",
        [" alpha", " beta"],
        model="model-a",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.save_continuations(
        "",
        [" gamma"],
        model="model-b",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
        parent_id=children[0].id,
    )
    store.set_active_node(children[0].id)

    result = runner.invoke(app, ["stats", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Total nodes" in result.output
    assert "model-a" in result.output
    assert "Path model" in result.output
    assert parent.id in result.output


def test_loom_stats_json(tmp_path) -> None:
    db = tmp_path / "generations.sqlite"
    store = GenerationStore(db)
    parent, children = store.save_continuations(
        "Seed",
        [" alpha"],
        model="model-a",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.set_active_node(children[0].id)

    result = runner.invoke(app, ["stats", parent.id, "--json", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert '"total_nodes": 2' in result.output
    assert '"model": "model-a"' in result.output


def test_loom_stats_can_analyze_json_file(tmp_path) -> None:
    path = tmp_path / "tinyloom.json"
    path.write_text(
        """
        {
          "root": 1,
          "nodes": {
            "1": {"id": 1, "parent": null, "type": "root", "timestamp": 1},
            "2": {
              "id": 2,
              "parent": 1,
              "type": "model-a",
              "timestamp": 2,
              "patches": [{"diffs": [[1, " alpha"]]}],
              "bookmarked": true
            }
          }
        }
        """
    )

    result = runner.invoke(app, ["stats", "--file", str(path), "--json"])

    assert result.exit_code == 0, result.output
    assert '"source_format"' not in result.output
    assert '"model": "model-a"' in result.output
    assert '"bookmarked": true' in result.output


def _seed_tree(db, root_text: str, branch_text: str) -> tuple[str, str]:
    store = GenerationStore(db)
    parent, children = store.save_continuations(
        root_text,
        [branch_text],
        model="m",
        strategy="system",
        max_tokens=5,
        temperature=0.9,
    )
    return parent.id, children[0].id


def test_import_adds_a_missing_tree_from_another_database(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, child_id = _seed_tree(source_db, "The ship rounded", " the headland")
    GenerationStore(target_db)

    result = runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    assert result.exit_code == 0, result.output
    target = GenerationStore(target_db)
    assert target.get(root_id) is not None
    assert target.full_text(child_id) == "The ship rounded the headland"
    assert set(target.tree_index()) == {root_id}


def test_import_is_repeatable_and_adds_only_what_is_missing(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, child_id = _seed_tree(source_db, "A", " B")
    GenerationStore(target_db)
    runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    source = GenerationStore(source_db)
    _, extra = source.save_continuations(
        " C",
        [" D"],
        model="m",
        strategy="system",
        max_tokens=5,
        temperature=0.9,
        parent_id=child_id,
    )
    result = runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    assert result.exit_code == 0, result.output
    target = GenerationStore(target_db)
    assert len(target.tree(root_id)) == len(source.tree(root_id))
    assert target.get(extra[0].id) is not None


def test_import_leaves_an_existing_tree_reading_where_it_was(tmp_path) -> None:
    """An import must not move the target's checked-out path."""
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, first_child = _seed_tree(source_db, "A", " B")
    runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])
    GenerationStore(target_db).set_checked_out_child(root_id, first_child)

    source = GenerationStore(source_db)
    _, added = source.save_continuations(
        "A",
        [" C"],
        model="m",
        strategy="system",
        max_tokens=5,
        temperature=0.9,
        parent_id=root_id,
    )
    source.set_checked_out_child(root_id, added[0].id)

    runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    target = GenerationStore(target_db)
    assert target.get_checked_out_child_id(root_id) == first_child
    assert target.get(added[0].id) is not None


def test_import_dry_run_writes_nothing(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, _ = _seed_tree(source_db, "A", " B")
    GenerationStore(target_db)

    result = runner.invoke(
        app, ["import", str(source_db), "--db", str(target_db), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "Would import" in unstyle(result.output)
    assert GenerationStore(target_db).get(root_id) is None


def test_import_can_be_limited_to_one_tree(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    wanted, _ = _seed_tree(source_db, "keep me", " yes")
    skipped, _ = _seed_tree(source_db, "leave me", " no")
    GenerationStore(target_db)

    result = runner.invoke(
        app, ["import", str(source_db), "--db", str(target_db), "--tree", wanted]
    )

    assert result.exit_code == 0, result.output
    target = GenerationStore(target_db)
    assert target.get(wanted) is not None
    assert target.get(skipped) is None


def test_import_round_trips_a_json_export(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, child_id = _seed_tree(source_db, "The ship rounded", " the headland")
    export = tmp_path / "tree.json"
    runner.invoke(
        app, ["export", "--db", str(source_db), "--node", root_id, "--to", str(export)]
    )
    GenerationStore(target_db)

    result = runner.invoke(app, ["import", str(export), "--db", str(target_db)])

    assert result.exit_code == 0, result.output
    assert GenerationStore(target_db).full_text(child_id) == (
        "The ship rounded the headland"
    )


def test_import_rejects_a_file_that_is_neither_export_nor_database(tmp_path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("just some prose")

    result = runner.invoke(
        app, ["import", str(junk), "--db", str(tmp_path / "t.sqlite")]
    )

    assert result.exit_code == 1
    assert "neither" in unstyle(result.output)


def test_import_carries_a_tree_name_across(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, _ = _seed_tree(source_db, "A", " B")
    source = GenerationStore(source_db)
    source.update_tree_settings(root_id, name="Hart Crane", global_n_branches=4)
    GenerationStore(target_db)

    result = runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    assert result.exit_code == 0, result.output
    tree = GenerationStore(target_db).get_tree(root_id)
    assert tree is not None
    assert tree.name == "Hart Crane"
    assert tree.global_n_branches == 4


def test_import_names_a_tree_left_nameless_by_an_earlier_import(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, _ = _seed_tree(source_db, "A", " B")
    source = GenerationStore(source_db)
    target = GenerationStore(target_db)
    target.import_nodes(source.tree(root_id))
    assert target.get_tree(root_id).name is None
    source.update_tree_settings(root_id, name="Punk")

    result = runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    assert result.exit_code == 0, result.output
    assert GenerationStore(target_db).get_tree(root_id).name == "Punk"


def test_import_never_renames_a_tree_that_already_has_a_name(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    root_id, _ = _seed_tree(source_db, "A", " B")
    source = GenerationStore(source_db)
    source.update_tree_settings(root_id, name="source name")
    runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])
    GenerationStore(target_db).update_tree_settings(root_id, name="my name for it")

    runner.invoke(app, ["import", str(source_db), "--db", str(target_db)])

    assert GenerationStore(target_db).get_tree(root_id).name == "my name for it"
