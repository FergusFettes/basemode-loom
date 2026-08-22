from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from ..config import DEFAULT_CONFIG, Config
from ..logging_utils import configure_logging
from ..store import GenerationStore
from ._rest import router
from ._security import (
    GenerationGate,
    RequestSecurityMiddleware,
    configured_origins,
    validate_server_config,
)
from ._stores import StoreResolver, fixed_store
from ._ws import session_ws


def _package_version() -> str:
    try:
        return version("basemode-loom")
    except PackageNotFoundError:
        return "0.0.0"


def create_app(
    store: GenerationStore,
    config: Config = DEFAULT_CONFIG,
    *,
    store_resolver: StoreResolver | None = None,
) -> FastAPI:
    """Create the Loom API application.

    ``store`` remains the default for the standalone server. An embedding
    application may provide ``store_resolver`` to choose a store for each HTTP
    request or WebSocket connection from trusted ASGI scope state. The
    resolver must never select a path from untrusted client input.
    """
    configure_logging("api")
    validate_server_config(config.server)
    allowed_origins = configured_origins(config.server)
    docs_enabled = (
        config.server.enable_docs
        if config.server.enable_docs is not None
        else not config.server.production
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.store = store
        app.state.store_resolver = store_resolver or fixed_store(store)
        app.state.config = config
        app.state.allowed_origins = allowed_origins
        app.state.generation_gate = GenerationGate(
            config.server.concurrent_generation_jobs
        )
        yield

    app = FastAPI(
        title="basemode-loom",
        version=_package_version(),
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RequestSecurityMiddleware,
        allowed_origins=allowed_origins,
        max_bytes=config.server.max_message_bytes,
    )
    app.include_router(router)

    @app.websocket("/ws/session")
    async def ws_session(websocket: WebSocket) -> None:
        store = websocket.app.state.store_resolver(websocket.scope)
        await session_ws(
            websocket,
            store,
            websocket.app.state.config.server,
            websocket.app.state.allowed_origins,
            websocket.app.state.generation_gate,
        )

    return app
