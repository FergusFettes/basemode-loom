"""Opt-in live-provider coverage for the loom generation lifecycle."""

from __future__ import annotations

import os

import pytest

from basemode_loom.session import GenerationComplete, GenerationError, LoomSession
from basemode_loom.store import GenerationStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_generates_and_persists_a_live_continuation(tmp_path) -> None:
    model = os.environ.get("BASEMODE_LOOM_INTEGRATION_MODEL")
    if not model:
        pytest.skip("set BASEMODE_LOOM_INTEGRATION_MODEL to enable live-provider tests")

    store = GenerationStore(tmp_path / "integration.sqlite")
    root = store.create_root("The lighthouse keeper wrote")
    session = LoomSession(store, root.id)
    session.set_model(model)
    session.set_max_tokens(16)

    events = [event async for event in session.generate()]
    errors = [event for event in events if isinstance(event, GenerationError)]
    assert not errors, errors
    complete = next(event for event in events if isinstance(event, GenerationComplete))
    assert complete.new_nodes
    assert store.children(root.id) == complete.new_nodes
