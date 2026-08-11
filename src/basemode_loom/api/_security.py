from __future__ import annotations

import asyncio
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import WebSocket
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import ServerConfig

DEVELOPMENT_ORIGINS = tuple(
    f"http://{host}{suffix}"
    for host in ("localhost", "127.0.0.1", "[::1]")
    for suffix in ("", ":3000", ":5173", ":8000", ":8080")
)


def configured_origins(config: ServerConfig) -> tuple[str, ...]:
    origins = (
        config.allowed_origins
        if config.production
        else [
            *DEVELOPMENT_ORIGINS,
            *config.allowed_origins,
        ]
    )
    return tuple(dict.fromkeys(_normalize_origin(origin) for origin in origins))


def validate_server_config(config: ServerConfig) -> None:
    if config.production and not config.allowed_origins:
        raise ValueError("production requires at least one explicitly allowed origin")
    configured_origins(config)
    positive = {
        "max_message_bytes": config.max_message_bytes,
        "max_field_bytes": config.max_field_bytes,
        "max_context_tokens": config.max_context_tokens,
        "concurrent_generation_jobs": config.concurrent_generation_jobs,
        "max_branches_per_job": config.max_branches_per_job,
        "generation_timeout_seconds": config.generation_timeout_seconds,
        "max_output_tokens": config.max_output_tokens,
    }
    for name, value in positive.items():
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if config.max_field_bytes > config.max_message_bytes:
        raise ValueError("max_field_bytes cannot exceed max_message_bytes")


def _normalize_origin(origin: str) -> str:
    value = origin.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"invalid HTTP origin: {origin!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"invalid HTTP origin: {origin!r}")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"origin must not contain a path: {origin!r}")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{host.lower()}{port}"


def origin_allowed(origin: str | None, allowed: Iterable[str]) -> bool:
    if origin is None:
        return True
    try:
        return _normalize_origin(origin) in allowed
    except ValueError:
        return False


def websocket_origin_allowed(
    websocket: WebSocket, config: ServerConfig, allowed: Iterable[str]
) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return not config.production
    return origin_allowed(origin, allowed)


def value_exceeds_field_limit(value: object, limit: int) -> bool:
    if isinstance(value, str):
        return len(value.encode("utf-8")) > limit
    if isinstance(value, dict):
        return any(value_exceeds_field_limit(item, limit) for item in value.values())
    if isinstance(value, list):
        return any(value_exceeds_field_limit(item, limit) for item in value)
    return False


def safe_error_message(_error: BaseException) -> str:
    return "The generation provider returned an error. Check server logs for details."


class GenerationGate:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)


class RequestSecurityMiddleware:
    def __init__(
        self, app: ASGIApp, *, allowed_origins: tuple[str, ...], max_bytes: int
    ):
        self.app = app
        self.allowed_origins = allowed_origins
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin_bytes = headers.get(b"origin")
        origin = origin_bytes.decode("latin-1") if origin_bytes else None
        if origin is not None and not origin_allowed(origin, self.allowed_origins):
            await _json_error(send, 403, "origin_not_allowed")
            return
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await _json_error(send, 413, "message_too_large")
                    return
            except ValueError:
                await _json_error(send, 400, "invalid_content_length")
                return

        messages: list[Message] = []
        seen = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            seen += len(message.get("body", b""))
            if seen > self.max_bytes:
                await _json_error(send, 413, "message_too_large")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


async def _json_error(send: Send, status: int, code: str) -> None:
    body = f'{{"detail":{{"code":"{code}"}}}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
