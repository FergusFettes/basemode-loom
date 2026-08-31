from dataclasses import dataclass

import pytest

from basemode_loom import basemode_adapter


def test_loom_observation_contains_only_allow_listed_provenance(monkeypatch) -> None:
    @dataclass(frozen=True)
    class FakeObservationContext:
        source: str
        source_version: str
        contribution_eligible: bool

    monkeypatch.setattr(basemode_adapter, "_ObservationContext", FakeObservationContext)
    monkeypatch.setattr(basemode_adapter, "_loom_version", lambda: "1.2.3")

    observation = basemode_adapter.loom_observation()

    assert observation == FakeObservationContext("loom", "1.2.3", False)
    assert set(vars(observation)) == {
        "source",
        "source_version",
        "contribution_eligible",
    }


@pytest.mark.asyncio
async def test_continue_text_forwards_observation_to_typed_basemode(
    monkeypatch,
) -> None:
    seen = {}

    async def fake_continue(*args, **kwargs):
        seen.update(kwargs)
        yield "done"

    monkeypatch.setattr(basemode_adapter, "_ObservationContext", object)
    monkeypatch.setattr(basemode_adapter, "_continue_text", fake_continue)
    observation = object()

    assert [
        token
        async for token in basemode_adapter.continue_text(
            "prefix", "model", observation=observation
        )
    ] == ["done"]
    assert seen == {"observation": observation}


@pytest.mark.asyncio
async def test_legacy_basemode_does_not_receive_unknown_observation(
    monkeypatch,
) -> None:
    seen = {}

    async def fake_continue(*args, **kwargs):
        seen.update(kwargs)
        yield "done"

    monkeypatch.setattr(basemode_adapter, "_ObservationContext", None)
    monkeypatch.setattr(basemode_adapter, "_continue_text", fake_continue)

    assert [
        token
        async for token in basemode_adapter.continue_text(
            "prefix", "model", observation=object()
        )
    ] == ["done"]
    assert seen == {}


@pytest.mark.asyncio
async def test_branch_text_forwards_one_context_for_distinct_operations(
    monkeypatch,
) -> None:
    seen = {}

    async def fake_branch(*args, **kwargs):
        seen.update(kwargs)
        yield 0, "first"
        yield 1, "second"

    monkeypatch.setattr(basemode_adapter, "_ObservationContext", object)
    monkeypatch.setattr(basemode_adapter, "_branch_text", fake_branch)
    observation = object()

    result = [
        item
        async for item in basemode_adapter.branch_text(
            "prefix", "model", n=2, observation=observation
        )
    ]

    assert result == [(0, "first"), (1, "second")]
    assert seen == {"n": 2, "observation": observation}
