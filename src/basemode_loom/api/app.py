from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from ..config import DEFAULT_CONFIG, Config
from ..logging_utils import configure_logging
from ..store import GenerationStore
from ._rest import router
from ._ws import session_ws


def create_app(store: GenerationStore, config: Config = DEFAULT_CONFIG) -> FastAPI:
    configure_logging("api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.store = store
        app.state.config = config
        yield

    app = FastAPI(title="basemode-loom", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.websocket("/ws/session")
    async def ws_session(websocket: WebSocket) -> None:
        await session_ws(websocket, websocket.app.state.store)

    return app
