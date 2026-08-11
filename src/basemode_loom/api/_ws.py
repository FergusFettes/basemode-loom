from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..config import ServerConfig
from ..logging_utils import get_logger
from ..session import (
    GenerationCancelled,
    GenerationComplete,
    GenerationError,
    LoomSession,
    TokenReceived,
)
from ..store import GenerationStore
from ._security import (
    GenerationGate,
    safe_error_message,
    value_exceeds_field_limit,
    websocket_origin_allowed,
)
from ._serialize import node_to_dict, state_to_dict

log = get_logger(__name__)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, int) or isinstance(value, float)) and not isinstance(
        value, bool
    )


def _validate_model_plan(raw: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, "must be a non-empty list"
    parsed: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        field = f"model_plan[{idx}]"
        if not isinstance(entry, dict):
            return None, f"{field} must be an object"
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip():
            return None, f"{field}.model must be a non-empty string"
        n_branches = entry.get("n_branches", 1)
        if not _is_int(n_branches) or n_branches < 1 or n_branches > 64:
            return None, f"{field}.n_branches must be an integer between 1 and 64"
        max_tokens = entry.get("max_tokens", 200)
        if not _is_int(max_tokens) or max_tokens < 10 or max_tokens > 8000:
            return None, f"{field}.max_tokens must be an integer between 10 and 8000"
        temperature = entry.get("temperature", 0.9)
        if (
            not _is_number(temperature)
            or math.isnan(float(temperature))
            or float(temperature) < 0.0
            or float(temperature) > 2.0
        ):
            return None, f"{field}.temperature must be a number between 0 and 2"
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            return None, f"{field}.enabled must be a boolean"
        parsed.append(
            {
                "model": model.strip(),
                "n_branches": int(n_branches),
                "max_tokens": int(max_tokens),
                "temperature": float(temperature),
                "enabled": enabled,
            }
        )
    return parsed, None


def _validate_set_params(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    allowed = {
        "type",
        "persist",
        "model",
        "max_tokens",
        "temperature",
        "n_branches",
        "context",
        "show_model_names",
        "model_plan",
    }
    patch: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for key in data:
        if key not in allowed:
            errors[key] = "unsupported field"

    if "persist" in data:
        if data["persist"] is not True:
            errors["persist"] = "only persist=true is supported"

    if "model" in data:
        model = data["model"]
        if not isinstance(model, str) or not model.strip():
            errors["model"] = "must be a non-empty string"
        else:
            patch["model"] = model.strip()

    if "max_tokens" in data:
        value = data["max_tokens"]
        if not _is_int(value) or value < 10 or value > 8000:
            errors["max_tokens"] = "must be an integer between 10 and 8000"
        else:
            patch["max_tokens"] = value

    if "temperature" in data:
        value = data["temperature"]
        if (
            not _is_number(value)
            or math.isnan(float(value))
            or float(value) < 0.0
            or float(value) > 2.0
        ):
            errors["temperature"] = "must be a number between 0 and 2"
        else:
            patch["temperature"] = float(value)

    if "n_branches" in data:
        value = data["n_branches"]
        if not _is_int(value) or value < 1 or value > 64:
            errors["n_branches"] = "must be an integer between 1 and 64"
        else:
            patch["n_branches"] = value

    if "context" in data:
        value = data["context"]
        if not isinstance(value, str):
            errors["context"] = "must be a string"
        else:
            patch["context"] = value

    if "show_model_names" in data:
        value = data["show_model_names"]
        if not isinstance(value, bool):
            errors["show_model_names"] = "must be a boolean"
        else:
            patch["show_model_names"] = value

    if "model_plan" in data:
        parsed_plan, error = _validate_model_plan(data["model_plan"])
        if error is not None:
            errors["model_plan"] = error
        else:
            patch["model_plan"] = parsed_plan

    if not patch and "persist" in data and len(data) == 2 and not errors:
        errors["set_params"] = "at least one config field is required"

    return patch, errors


async def session_ws(
    websocket: WebSocket,
    store: GenerationStore,
    config: ServerConfig,
    allowed_origins: tuple[str, ...],
    generation_gate: GenerationGate,
) -> None:
    if not websocket_origin_allowed(websocket, config, allowed_origins):
        await websocket.close(code=1008, reason="origin not allowed")
        return
    await websocket.accept()
    session: LoomSession | None = None
    gen_task: asyncio.Task[None] | None = None

    async def push_state() -> None:
        if session is not None:
            await websocket.send_json(
                {"type": "state", "state": state_to_dict(session.get_state())}
            )

    async def send_error(message: str, *, error_type: str = "error") -> None:
        await websocket.send_json({"type": error_type, "message": message})

    async def run_generation() -> None:
        assert session is not None
        acquired = await generation_gate.try_acquire()
        if not acquired:
            await send_error(
                "generation capacity is busy", error_type="generation_busy"
            )
            return
        try:
            async with asyncio.timeout(config.generation_timeout_seconds):
                async for event in session.generate(
                    max_context_tokens=config.max_context_tokens
                ):
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
                        tree = store.tree_for_node(root.id) if root else None
                        if root and tree and tree.name:
                            await websocket.send_json(
                                {
                                    "type": "tree_named",
                                    "root_id": root.id,
                                    "name": tree.name,
                                }
                            )
                    elif isinstance(event, GenerationError):
                        response: dict[str, Any] = {
                            "type": "generation_error",
                            "error": safe_error_message(event.error),
                        }
                        if event.incident_id is not None:
                            response["incident_id"] = event.incident_id
                        if event.category is not None:
                            response["category"] = event.category
                        if event.status is not None:
                            response["status"] = event.status
                        await websocket.send_json(response)
                    elif isinstance(event, GenerationCancelled):
                        await websocket.send_json({"type": "generation_cancelled"})
                        await push_state()
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            session.cancel()
            await send_error("generation timed out", error_type="generation_timeout")
        except Exception:
            log.error("websocket generation loop failed")
            await send_error("generation failed", error_type="generation_error")
        finally:
            await generation_gate.release()

    try:
        while True:
            message = await websocket.receive()
            raw = message.get("text")
            try:
                if raw is None and message.get("bytes") is not None:
                    raw = message["bytes"].decode("utf-8")
            except UnicodeDecodeError:
                await websocket.close(code=1007, reason="invalid UTF-8 payload")
                return
            if raw is None:
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                continue
            if len(raw.encode("utf-8")) > config.max_message_bytes:
                await websocket.close(code=1009, reason="message too large")
                return
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                await send_error("invalid JSON", error_type="invalid_message")
                continue
            if not isinstance(data, dict):
                await send_error(
                    "message must be an object", error_type="invalid_message"
                )
                continue
            if value_exceeds_field_limit(data, config.max_field_bytes):
                await websocket.close(code=1009, reason="field too large")
                return
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
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            elif msg_type == "set_params":
                patch, field_errors = _validate_set_params(data)
                if field_errors:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "invalid set_params",
                            "fields": field_errors,
                        }
                    )
                    continue
                session.apply_config_patch(patch)
                session.persist_config()
                await push_state()

            elif msg_type == "generate":
                if gen_task and not gen_task.done():
                    await send_error("generation already in progress")
                else:
                    state = session.get_state()
                    branches = sum(
                        plan.n_branches for plan in state.model_plan if plan.enabled
                    )
                    if branches > config.max_branches_per_job:
                        await send_error(
                            "generation branch limit exceeded",
                            error_type="generation_limit_exceeded",
                        )
                    elif any(
                        plan.max_tokens > config.max_output_tokens
                        for plan in state.model_plan
                        if plan.enabled
                    ):
                        await send_error(
                            "generation output token limit exceeded",
                            error_type="generation_limit_exceeded",
                        )
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
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            elif msg_type == "view_toggle":
                state = session.toggle_tree_view()
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            elif msg_type == "hoist_toggle":
                state = session.toggle_hoist()
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            elif msg_type == "model_names_toggle":
                state = session.toggle_model_names()
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            elif msg_type == "chat_headers_toggle":
                state = session.toggle_chat_headers()
                await websocket.send_json(
                    {"type": "state", "state": state_to_dict(state)}
                )

            else:
                await send_error(f"unknown message type: {msg_type!r}")

    except WebSocketDisconnect:
        log.info("websocket disconnected")
    finally:
        if gen_task and not gen_task.done():
            gen_task.cancel()
            await asyncio.gather(gen_task, return_exceptions=True)
