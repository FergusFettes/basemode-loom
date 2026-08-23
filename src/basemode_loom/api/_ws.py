from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from basemode.health import record_outcome
from fastapi import WebSocket, WebSocketDisconnect

from ..config import ServerConfig
from ..logging_utils import get_logger
from ..model_plan import MAX_MAX_TOKENS, MIN_MAX_TOKENS, validate_model_plan
from ..model_resolver import resolve_model_id
from ..session import (
    BranchComplete,
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


def _validate_set_params(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    allowed = {
        "type",
        "persist",
        "model",
        "max_tokens",
        "temperature",
        "n_branches",
        "global_max_tokens",
        "global_n_branches",
        "rewind_split_tokens",
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
        if not _is_int(value) or value < MIN_MAX_TOKENS or value > MAX_MAX_TOKENS:
            errors["max_tokens"] = (
                f"must be an integer between {MIN_MAX_TOKENS} and {MAX_MAX_TOKENS}"
            )
        else:
            patch["max_tokens"] = value

    if "temperature" in data:
        value = data["temperature"]
        if (
            not _is_number(value)
            or not math.isfinite(float(value))
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

    if "global_max_tokens" in data:
        value = data["global_max_tokens"]
        if not _is_int(value) or value < MIN_MAX_TOKENS or value > MAX_MAX_TOKENS:
            errors["global_max_tokens"] = (
                f"must be an integer between {MIN_MAX_TOKENS} and {MAX_MAX_TOKENS}"
            )
        else:
            patch["global_max_tokens"] = value

    if "global_n_branches" in data:
        value = data["global_n_branches"]
        if not _is_int(value) or value < 1 or value > 64:
            errors["global_n_branches"] = "must be an integer between 1 and 64"
        else:
            patch["global_n_branches"] = value

    if "rewind_split_tokens" in data:
        value = data["rewind_split_tokens"]
        if not isinstance(value, bool):
            errors["rewind_split_tokens"] = "must be a boolean"
        else:
            patch["rewind_split_tokens"] = value

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
        parsed_plan, error = validate_model_plan(data["model_plan"])
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
    # Several generations can be in flight at once, typically started from
    # different places in the tree. `concurrent_generations_per_session` caps
    # this connection; the server-wide `generation_gate` caps everything.
    gen_tasks: set[asyncio.Task[None]] = set()

    async def cancel_gen_tasks() -> None:
        running = [task for task in gen_tasks if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        gen_tasks.clear()

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
        # Keep the job's original plan so a whole-job timeout can be reported
        # as ordinary per-model failures. The browser can then mark only the
        # providers that still had unfinished branches, rather than showing a
        # context-free timeout toast.
        pending_branches: dict[tuple[int, int], str] = {}
        for model_idx, plan in enumerate(session.model_plan):
            if not plan.enabled:
                continue
            branch_count = (
                session.global_n_branches if plan.pinned_settings else plan.n_branches
            )
            for branch_idx in range(branch_count):
                pending_branches[(model_idx, branch_idx)] = plan.model
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
                    elif isinstance(event, BranchComplete):
                        pending_branches.pop((event.model_idx, event.branch_idx), None)
                        # Push the branch and the state it lands in right
                        # away, so this continuation is selectable while its
                        # slower siblings are still streaming.
                        await websocket.send_json(
                            {
                                "type": "branch_complete",
                                "model_idx": event.model_idx,
                                "branch_idx": event.branch_idx,
                                "slot_idx": event.slot_idx,
                                "node": node_to_dict(event.node),
                            }
                        )
                        await websocket.send_json(
                            {
                                "type": "state",
                                "state": state_to_dict(session.get_state()),
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
                        failures = event.failures or (None,)
                        for failure in failures:
                            response: dict[str, Any] = {
                                "type": "generation_error",
                                "error": safe_error_message(event.error),
                            }
                            incident_id = (
                                failure.incident_id if failure else event.incident_id
                            )
                            category = failure.category if failure else event.category
                            status = failure.status if failure else event.status
                            finish_reason = (
                                failure.finish_reason
                                if failure
                                else event.finish_reason
                            )
                            if incident_id is not None:
                                response["incident_id"] = incident_id
                            if category is not None:
                                response["category"] = category
                            if status is not None:
                                response["status"] = status
                            if finish_reason is not None:
                                response["finish_reason"] = finish_reason
                            if failure is not None:
                                pending_branches.pop(
                                    (failure.model_idx, failure.branch_idx), None
                                )
                                response.update(
                                    model=failure.model,
                                    model_idx=failure.model_idx,
                                    branch_idx=failure.branch_idx,
                                    slot_idx=failure.slot_idx,
                                )
                            await websocket.send_json(response)
                    elif isinstance(event, GenerationCancelled):
                        await websocket.send_json({"type": "generation_cancelled"})
                        await push_state()
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            session.cancel()
            for (model_idx, branch_idx), model in pending_branches.items():
                record_outcome(resolve_model_id(model), ok=False, category="timeout")
                await websocket.send_json(
                    {
                        "type": "generation_error",
                        "error": "generation timed out",
                        "model": model,
                        "model_idx": model_idx,
                        "branch_idx": branch_idx,
                        "category": "timeout",
                    }
                )
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
                await cancel_gen_tasks()
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

            elif msg_type == "checkout":
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("checkout requires a node_id")
                    continue
                try:
                    state = session.checkout(node_id)
                except (KeyError, ValueError) as exc:
                    await send_error(str(exc))
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
                gen_tasks.difference_update({task for task in gen_tasks if task.done()})
                if len(gen_tasks) >= config.concurrent_generations_per_session:
                    await send_error(
                        f"{config.concurrent_generations_per_session} generations "
                        "already running",
                        error_type="generation_busy",
                    )
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
                        task = asyncio.create_task(run_generation())
                        gen_tasks.add(task)
                        task.add_done_callback(gen_tasks.discard)

            elif msg_type == "cancel":
                session.cancel()

            elif msg_type == "edit":
                session.apply_edit(
                    str(data.get("original", "")), str(data.get("edited", ""))
                )
                await push_state()

            elif msg_type == "edit_node":
                node_id = data.get("node_id")
                text = data.get("text")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("edit_node requires a node_id")
                    continue
                if not isinstance(text, str):
                    await send_error("edit_node requires text")
                    continue
                # A no-op edit also returns None, so an unchanged tree here is
                # success, not an error; just resend state either way.
                session.edit_node_text(node_id, text, heal_boundary=True)
                await push_state()

            elif msg_type == "remove_leading_space":
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("remove_leading_space requires a node_id")
                    continue
                # This narrowly corrects an unwanted generated token boundary;
                # unlike edit_node it preserves node identity and descendants.
                session.remove_leading_space(node_id)
                await push_state()

            elif msg_type == "add_node":
                parent_id = data.get("parent_id")
                text = data.get("text")
                if not isinstance(parent_id, str) or not parent_id:
                    await send_error("add_node requires a parent_id")
                    continue
                if not isinstance(text, str):
                    await send_error("add_node requires text")
                    continue
                if session.add_child_node(parent_id, text) is None:
                    await send_error(f"unknown parent node: {parent_id!r}")
                    continue
                await push_state()

            elif msg_type == "delete_node":
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("delete_node requires a node_id")
                    continue
                if session.delete_node(node_id) is None:
                    await send_error(f"could not delete node: {node_id!r}")
                    continue
                await push_state()

            elif msg_type == "bookmark_node":
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("bookmark_node requires a node_id")
                    continue
                if session.toggle_node_bookmark(node_id) is None:
                    await send_error(f"unknown node: {node_id!r}")
                    continue
                await push_state()

            elif msg_type == "flag_node":
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    await send_error("flag_node requires a node_id")
                    continue
                if session.toggle_node_flag(node_id) is None:
                    await send_error(f"unknown node: {node_id!r}")
                    continue
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
        await cancel_gen_tasks()
