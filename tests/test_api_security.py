from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from basemode_loom.api.app import create_app
from basemode_loom.config import Config, ServerConfig, load_config
from basemode_loom.session import GenerationError, LoomSession, TokenReceived
from basemode_loom.store import GenerationStore

ALLOWED = "https://grove.example.com"


def _production_config(**overrides) -> Config:
    values = {
        "production": True,
        "allowed_origins": [ALLOWED],
        "max_message_bytes": 1024,
        "max_field_bytes": 512,
        "max_context_tokens": 4096,
        "concurrent_generation_jobs": 1,
        "max_branches_per_job": 8,
        "generation_timeout_seconds": 5,
        "max_output_tokens": 1000,
    }
    values.update(overrides)
    return Config(server=ServerConfig(**values))


def _store(tmp_path) -> tuple[GenerationStore, str]:
    store = GenerationStore(tmp_path / "security.sqlite")
    return store, store.create_root("seed").id


def _init(ws, root_id: str) -> None:
    ws.send_json({"type": "init", "root_id": root_id})
    assert ws.receive_json()["type"] == "state"


def test_production_requires_explicit_origins(tmp_path) -> None:
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError, match="explicitly allowed origin"):
        create_app(store, Config(server=ServerConfig(production=True)))


def test_disallowed_http_origin_is_rejected_but_missing_origin_is_allowed(
    tmp_path,
) -> None:
    store, _ = _store(tmp_path)
    with TestClient(create_app(store, _production_config())) as client:
        denied = client.get("/api/roots", headers={"Origin": "https://attacker.test"})
        allowed = client.get("/api/roots")

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "origin_not_allowed"
    assert allowed.status_code == 200


def test_production_websocket_requires_allowed_origin(tmp_path) -> None:
    store, _ = _store(tmp_path)
    with TestClient(create_app(store, _production_config())) as client:
        with pytest.raises(WebSocketDisconnect) as missing:
            with client.websocket_connect("/ws/session"):
                pass
        with pytest.raises(WebSocketDisconnect) as unrelated:
            with client.websocket_connect(
                "/ws/session", headers={"Origin": "https://attacker.test"}
            ):
                pass
        with client.websocket_connect(
            "/ws/session", headers={"Origin": ALLOWED}
        ) as websocket:
            assert websocket is not None

    assert missing.value.code == 1008
    assert unrelated.value.code == 1008


def test_oversized_http_and_websocket_messages_are_rejected(tmp_path) -> None:
    store, _ = _store(tmp_path)
    config = _production_config(max_message_bytes=128, max_field_bytes=64)
    with TestClient(create_app(store, config)) as client:
        response = client.post(
            "/api/import",
            content=b"x" * 129,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "message_too_large"

        with client.websocket_connect(
            "/ws/session", headers={"Origin": ALLOWED}
        ) as websocket:
            websocket.send_text("x" * 129)
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1009


def test_production_disables_documentation_surfaces(tmp_path) -> None:
    store, _ = _store(tmp_path)
    with TestClient(create_app(store, _production_config())) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_branch_and_output_limits_are_checked_before_generation(tmp_path) -> None:
    store, root_id = _store(tmp_path)
    config = _production_config(max_branches_per_job=2, max_output_tokens=100)
    with TestClient(create_app(store, config)) as client:
        with client.websocket_connect(
            "/ws/session", headers={"Origin": ALLOWED}
        ) as websocket:
            _init(websocket, root_id)
            websocket.send_json(
                {
                    "type": "set_params",
                    "persist": True,
                    "model_plan": [
                        {"model": "a", "n_branches": 2, "max_tokens": 50},
                        {"model": "b", "n_branches": 1, "max_tokens": 50},
                    ],
                }
            )
            assert websocket.receive_json()["type"] == "state"
            websocket.send_json({"type": "generate"})
            assert websocket.receive_json()["type"] == "generation_limit_exceeded"

            websocket.send_json(
                {
                    "type": "set_params",
                    "persist": True,
                    "model_plan": [{"model": "a", "n_branches": 1, "max_tokens": 101}],
                }
            )
            assert websocket.receive_json()["type"] == "state"
            websocket.send_json({"type": "generate"})
            assert websocket.receive_json()["type"] == "generation_limit_exceeded"


def test_excess_concurrent_generation_is_rejected_immediately(
    tmp_path, monkeypatch
) -> None:
    store, root_id = _store(tmp_path)

    async def blocked_generate(self, **_kwargs):
        yield TokenReceived(0, 0, 0, "started")
        await asyncio.Event().wait()

    monkeypatch.setattr(LoomSession, "generate", blocked_generate)

    with TestClient(create_app(store, _production_config())) as client:
        with (
            client.websocket_connect(
                "/ws/session", headers={"Origin": ALLOWED}
            ) as first,
            client.websocket_connect(
                "/ws/session", headers={"Origin": ALLOWED}
            ) as second,
        ):
            _init(first, root_id)
            _init(second, root_id)
            first.send_json({"type": "generate"})
            assert first.receive_json()["type"] == "token"
            second.send_json({"type": "generate"})
            assert second.receive_json()["type"] == "generation_busy"


def test_provider_error_response_is_sanitized(tmp_path, monkeypatch) -> None:
    store, root_id = _store(tmp_path)
    secret = "sk-this-must-never-escape"

    async def failed_generate(self, **_kwargs):
        yield GenerationError(RuntimeError(f"api_key={secret}; provider metadata"))

    monkeypatch.setattr(LoomSession, "generate", failed_generate)
    with TestClient(create_app(store, _production_config())) as client:
        with client.websocket_connect(
            "/ws/session", headers={"Origin": ALLOWED}
        ) as websocket:
            _init(websocket, root_id)
            websocket.send_json({"type": "generate"})
            response = websocket.receive_json()

    assert response["type"] == "generation_error"
    assert secret not in str(response)
    assert "provider metadata" not in str(response)


def test_malformed_binary_websocket_cancels_running_generation(
    tmp_path, monkeypatch
) -> None:
    store, root_id = _store(tmp_path)
    cancelled = threading.Event()

    async def blocked_generate(self, **_kwargs):
        try:
            yield TokenReceived(0, 0, 0, "started")
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(LoomSession, "generate", blocked_generate)
    with TestClient(create_app(store)) as client:
        with client.websocket_connect("/ws/session") as websocket:
            _init(websocket, root_id)
            websocket.send_json({"type": "generate"})
            assert websocket.receive_json()["type"] == "token"
            websocket.send_bytes(b"\xff")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1007

    assert cancelled.wait(1)


@pytest.mark.asyncio
async def test_provider_failure_has_safe_diagnostic_metadata(
    tmp_path, monkeypatch
) -> None:
    store, root_id = _store(tmp_path)

    class RateLimitError(Exception):
        status_code = 429

    async def failed_provider(*args, **kwargs):
        raise RateLimitError("api_key=sk-secret provider body")
        yield  # pragma: no cover

    monkeypatch.setattr("basemode_loom.session.continue_text", failed_provider)
    events = [event async for event in LoomSession(store, root_id).generate()]
    error = next(event for event in events if isinstance(event, GenerationError))

    assert error.category == "rate_limit"
    assert error.status == 429
    assert error.incident_id is not None
    assert "sk-secret" not in str(error.error)


def test_server_security_config_is_not_exposed(tmp_path) -> None:
    store, _ = _store(tmp_path)
    config = _production_config()
    with TestClient(create_app(store, config)) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    assert "server" not in response.json()
    assert ALLOWED not in response.text


def test_environment_overrides_server_toml(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".basemode-loom.toml").write_text(
        '[server]\nallowed_origins = ["https://toml.example"]\n'
        "concurrent_generation_jobs = 3\n"
    )
    monkeypatch.setenv("BASEMODE_LOOM_ALLOWED_ORIGINS", ALLOWED)
    monkeypatch.setenv("BASEMODE_LOOM_CONCURRENT_GENERATION_JOBS", "1")

    config = load_config()

    assert config.server.allowed_origins == [ALLOWED]
    assert config.server.concurrent_generation_jobs == 1


@pytest.mark.asyncio
async def test_assembled_prompt_limit_is_checked_before_provider_call(
    tmp_path, monkeypatch
) -> None:
    store, root_id = _store(tmp_path)
    called = False

    async def forbidden_provider(*args, **kwargs):
        nonlocal called
        called = True
        yield "unexpected"

    monkeypatch.setattr("basemode_loom.session.continue_text", forbidden_provider)
    events = [
        event
        async for event in LoomSession(store, root_id).generate(max_context_tokens=1)
    ]

    assert len(events) == 1
    assert isinstance(events[0], GenerationError)
    assert called is False
