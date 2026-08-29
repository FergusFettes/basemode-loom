"""Installed package versions over the API.

A client pairs these with its own build to answer "are we looking at the same
thing?", so a package that cannot be found reports null rather than a
placeholder that would read as a real version.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from basemode_loom.api.app import create_app
from basemode_loom.config import Config
from basemode_loom.store import GenerationStore


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(GenerationStore(tmp_path / "versions.sqlite"), Config())
    )


def test_reports_the_installed_versions(tmp_path) -> None:
    with _client(tmp_path) as client:
        body = client.get("/api/version").json()

    from importlib.metadata import version

    assert body["basemode_loom"] == version("basemode-loom")
    assert body["basemode"] == version("basemode")


def test_a_missing_package_reports_null(tmp_path, monkeypatch) -> None:
    from importlib.metadata import PackageNotFoundError

    def absent(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("basemode_loom.api._rest.version", absent)
    with _client(tmp_path) as client:
        body = client.get("/api/version").json()

    assert body == {"basemode": None, "basemode_loom": None}
