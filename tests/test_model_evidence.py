from __future__ import annotations

import json
import sys
import types

from typer.testing import CliRunner

from basemode_loom.cli import app
from basemode_loom.model_evidence import (
    collect_corpus_observations,
    publish_corpus_statistics,
)
from basemode_loom.session import LoomSession
from basemode_loom.store import GenerationStore


def _add(store: GenerationStore, parent_id: str, model: str = "provider/model"):
    return store.add_child(
        parent_id,
        " continuation",
        model=model,
        strategy="system",
        max_tokens=20,
        temperature=0.8,
    )


def test_corpus_evidence_separates_open_and_corrected_issues(tmp_path) -> None:
    store = GenerationStore(tmp_path / "loom.sqlite")
    root = store.create_root("A beginning")
    clean = _add(store, root.id)
    open_issue = _add(store, clean.id)
    corrected = _add(store, open_issue.id)
    store.update_metadata(open_issue.id, {"flagged": True})
    session = LoomSession(store, root.id)
    assert session.remove_leading_space(corrected.id) is not None
    store.update_metadata(
        clean.id,
        {
            "timing": {
                "ttft_ms": 10,
                "elapsed_ms": 40,
                "completion_tokens_per_second": 25,
                "completion_tokens": 1,
            }
        },
    )

    observations = collect_corpus_observations(store)

    by_kind = {item.issue_kind: item for item in observations}
    assert by_kind["none"].generated_count == 1
    assert by_kind["none"].prompt_method == "system"
    assert by_kind["none"].timed_count == 1
    assert by_kind["manual_flag"].open_issue_count == 1
    assert by_kind["manual_flag"].corrected_count == 0
    assert by_kind["boundary_edit"].corrected_count == 1
    assert by_kind["boundary_edit"].open_issue_count == 0
    assert sum(item.flagged_count for item in observations) == 2


def test_corpus_evidence_uses_depth_buckets_and_time_windows(tmp_path) -> None:
    store = GenerationStore(tmp_path / "loom.sqlite")
    node = store.create_root("root")
    for _ in range(30):
        node = _add(store, node.id)

    observations = collect_corpus_observations(store)

    assert {item.depth_bucket for item in observations} == {
        "0-4",
        "5-14",
        "15-29",
        "30+",
    }
    assert sum(item.generated_count for item in observations) == 30
    assert collect_corpus_observations(store, window_start="9999-01-01T00:00:00Z") == []


def test_publish_uses_basemode_adapter_without_private_material(
    tmp_path, monkeypatch
) -> None:
    store = GenerationStore(tmp_path / "private-name.sqlite")
    root = store.create_root("secret prompt")
    child = _add(store, root.id)
    store.update_metadata(child.id, {"flagged": True})
    captured = {}

    evidence = types.ModuleType("basemode.evidence")

    def fake_publish(observations, **metadata):
        captured["observations"] = observations
        captured["metadata"] = metadata
        return len(observations)

    evidence.publish_corpus_observations = fake_publish  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "basemode.evidence", evidence)

    assert publish_corpus_statistics(store, source_instance="test-corpus") == 1
    payload = json.dumps(captured, sort_keys=True)
    assert "secret prompt" not in payload
    assert root.id not in payload
    assert child.id not in payload
    assert "private-name" not in payload
    assert captured["metadata"]["source_instance"] == "test-corpus"


def test_publish_evidence_dry_run_is_aggregate_only(tmp_path) -> None:
    store = GenerationStore(tmp_path / "loom.sqlite")
    root = store.create_root("private text")
    child = _add(store, root.id)

    result = CliRunner().invoke(
        app, ["publish-evidence", "--db", str(store.db_path), "--dry-run"]
    )

    assert result.exit_code == 0
    assert "provider/model" in result.stdout
    assert "private text" not in result.stdout
    assert child.id not in result.stdout
