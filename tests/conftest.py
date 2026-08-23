from __future__ import annotations

import pytest
from basemode import health

from basemode_loom.store import GenerationStore


@pytest.fixture(autouse=True)
def isolated_health_store(tmp_path, monkeypatch) -> None:
    """Keep the suite off the developer's real ~/.config/basemode/health.sqlite.

    Generating in a test is still a recorded attempt as far as basemode is
    concerned, so without this every run would file dozens of fake models and
    invented failure rates against the machine's real history.
    """
    monkeypatch.setattr(health, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(health, "_DB_FILE", tmp_path / "health.sqlite")


@pytest.fixture
def store(tmp_path) -> GenerationStore:
    return GenerationStore(tmp_path / "test.sqlite")
