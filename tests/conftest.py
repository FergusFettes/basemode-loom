from __future__ import annotations

import pytest

from basemode_loom.store import GenerationStore


@pytest.fixture
def store(tmp_path) -> GenerationStore:
    return GenerationStore(tmp_path / "test.sqlite")
