from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..session import (
    GenerationCancelled,
    GenerationComplete,
    GenerationError,
    LoomSession,
    TokenReceived,
)
from ..store import GenerationStore
from ._serialize import node_to_dict, state_to_dict


async def session_ws(websocket: WebSocket, store: GenerationStore) -> None:
    await websocket.accept()
    session: LoomSession | None = None
    gen_task: asyncio.Task[None] | None = None

    async def push_state() -> None:
        if session is not None:
            await websocket.send_json(
                {"type": "state", "state": state_to_dict(session.get_state())}
            )

    async def send_error(message: str) -> None:
        await websocket.send_json({"type": "error", "message": message})

    async def run_generation() -> None:
        assert session is not None
        try:
            async for event in session.generate():
                if isinstance(event, TokenReceived):
                    await websocket.send_json(
                        {
                            "type": "token",
                            "model_idx": event.model_idx,
                            "branch_idx": event.branch_idx,
                            "slot_idx": event.slot_idx,
                            "text": event.token,
                        }
                    )
                elif isinstance(event, GenerationComplete):
                    await websocket.send_json(
                        {
                            "type": "generation_complete",
                            "new_nodes": [node_to_dict(n) for n in event.new_nodes],
                        }
                    )
                    state = session.get_state()
                    await websocket.send_json(
                        {"type": "state", "state": state_to_dict(state)}
                    )
                    root = store.get(state.root_id)
                    if root and root.metadata.get("name"):
                        await websocket.send_json(
                            {
                                "type": "tree_named",
                                "root_id": root.id,
                                "name": root.metadata["name"],
                            }
                        )
                elif isinstance(event, GenerationError):
                    await websocket.send_json(
                        {"type": "generation_error", "error": str(event.error)}
                    )
                elif isinstance(event, GenerationCancelled):
                    await websocket.send_json({"type": "generation_cancelled"})
                    await push_state()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await send_error(str(exc))

    try:
        while True:
            data: dict[str, Any] = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "init":
                root_id = data.get("root_id")
                if not root_id or store.get(root_id) is None:
                    await send_error(f"unknown root_id: {root_id!r}")
                    continue
                if gen_task and not gen_task.done():
                    gen_task.cancel()
                    await asyncio.gather(gen_task, return_exceptions=True)
                session = LoomSession(store, root_id)
                await push_state()
                continue

            if session is None:
                await send_error('send {"type": "init", "root_id": "..."} first')
                continue

            if msg_type == "navigate":
                direction = data.get("direction")
                if direction == "child":
                    state = session.navigate_child()
                elif direction == "parent":
                    state = session.navigate_parent()
                elif direction == "next_sibling":
                    state = session.select_sibling(+1)
                elif direction == "prev_sibling":
                    state = session.select_sibling(-1)
                else:
                    await send_error(f"unknown direction: {direction!r}")
                    continue
                await websocket.send_json({"type": "state", "state": state_to_dict(state)})

            elif msg_type == "set_params":
                if "model_plan" in data and isinstance(data["model_plan"], list):
                    session.set_model_plan(data["model_plan"])
                if "model" in data:
                    session.set_model(str(data["model"]))
                if "max_tokens" in data:
                    session.set_max_tokens(int(data["max_tokens"]))
                if "temperature" in data:
                    session.temperature = float(data["temperature"])
                if "n_branches" in data:
                    session.set_n_branches(int(data["n_branches"]))
                if "context" in data:
                    session.update_context(str(data["context"]))
                await push_state()

            elif msg_type == "generate":
                if gen_task and not gen_task.done():
                    await send_error("generation already in progress")
                else:
                    gen_task = asyncio.create_task(run_generation())

            elif msg_type == "cancel":
                session.cancel()

            elif msg_type == "edit":
                session.apply_edit(
                    str(data.get("original", "")), str(data.get("edited", ""))
                )
                await push_state()

            elif msg_type == "bookmark_toggle":
                session.toggle_bookmark()
                await push_state()

            elif msg_type == "bookmark_next":
                state = session.next_bookmark()
                await websocket.send_json({"type": "state", "state": state_to_dict(state)})

            elif msg_type == "view_toggle":
                state = session.toggle_tree_view()
                await websocket.send_json({"type": "state", "state": state_to_dict(state)})

            elif msg_type == "hoist_toggle":
                state = session.toggle_hoist()
                await websocket.send_json({"type": "state", "state": state_to_dict(state)})

            elif msg_type == "model_names_toggle":
                state = session.toggle_model_names()
                await websocket.send_json({"type": "state", "state": state_to_dict(state)})

            else:
                await send_error(f"unknown message type: {msg_type!r}")

    except WebSocketDisconnect:
        if gen_task and not gen_task.done():
            gen_task.cancel()
            await asyncio.gather(gen_task, return_exceptions=True)
