import importlib


def test_default_log_path_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BASEMODE_LOG", str(tmp_path / "custom.log"))
    mod = importlib.import_module("basemode_loom.logging_utils")
    importlib.reload(mod)
    assert mod.default_log_path() == tmp_path / "custom.log"


def test_configure_logging_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("BASEMODE_LOG", str(tmp_path / "loom.log"))
    mod = importlib.import_module("basemode_loom.logging_utils")
    importlib.reload(mod)

    first = mod.configure_logging("test")
    second = mod.configure_logging("test")

    assert first == second
    assert first.parent.exists()
