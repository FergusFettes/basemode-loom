from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import Config, config_to_dict
from ..stats import analyze_tree
from ..store import GenerationStore, Node
from ._serialize import node_to_dict, tree_to_dict

router = APIRouter(prefix="/api")


def _get_store(request: Request) -> GenerationStore:
    return request.app.state.store


def _get_config(request: Request) -> Config:
    return request.app.state.config


StoreDep = Annotated[GenerationStore, Depends(_get_store)]


@router.get("/config")
async def get_config(request: Request) -> dict:
    return config_to_dict(_get_config(request))


def _root_summary(store: GenerationStore, root: Node) -> dict[str, Any]:
    tree = store.tree_for_node(root.id)
    return {
        "id": root.id,
        "tree_id": root.tree_id,
        "text": root.text[:200],
        "name": tree.name,
        "created_at": root.created_at,
        "descendant_count": store.descendant_count(root.id),
    }


class CreateRootBody(BaseModel):
    text: str
    name: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    n_branches: int | None = None
    context: str | None = None


@router.get("/roots")
def list_roots(store: StoreDep) -> list[dict]:
    return [_root_summary(store, r) for r in store.roots()]


@router.post("/roots", status_code=201)
def create_root(body: CreateRootBody, store: StoreDep) -> dict:
    meta: dict[str, Any] = {}
    for key in ("name", "model", "max_tokens", "n_branches", "context"):
        val = getattr(body, key)
        if val is not None:
            meta[key] = val
    if body.temperature is not None:
        meta["temperature"] = body.temperature
    root = store.create_root(body.text, metadata=meta)
    return _root_summary(store, root)


@router.delete("/roots/{root_id}")
def delete_root(root_id: str, store: StoreDep) -> dict:
    if store.get(root_id) is None:
        raise HTTPException(status_code=404, detail="root not found")
    store.delete_tree(root_id)
    return {"ok": True}


@router.get("/roots/{root_id}/tree")
def get_tree(root_id: str, store: StoreDep) -> dict:
    if store.get(root_id) is None:
        raise HTTPException(status_code=404, detail="root not found")
    tree = store.tree_for_node(root_id)
    return {
        "tree": tree_to_dict(tree),
        "nodes": [node_to_dict(n) for n in store.tree(root_id)],
    }


@router.get("/roots/{root_id}/stats")
def get_stats(root_id: str, store: StoreDep) -> dict:
    if store.get(root_id) is None:
        raise HTTPException(status_code=404, detail="root not found")
    return analyze_tree(store, root_id).as_dict()


@router.get("/roots/{root_id}/export")
def export_tree(root_id: str, store: StoreDep) -> dict:
    if store.get(root_id) is None:
        raise HTTPException(status_code=404, detail="root not found")
    return {"version": 1, "nodes": [node_to_dict(n) for n in store.tree(root_id)]}


@router.get("/nodes/{node_id}")
def get_node(node_id: str, store: StoreDep) -> dict:
    node = store.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return {**node_to_dict(node), "full_text": store.full_text(node_id)}


@router.get("/models")
def list_models() -> dict:
    try:
        import basemode.models as bm  # type: ignore[import]

        picker = getattr(bm, "list_model_picker_entries", None)
        if callable(picker):
            return {"models": picker(available_only=True)}
        return {"models": bm.list_models(available_only=True)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import", status_code=201)
def import_tree(body: dict, store: StoreDep) -> dict:
    nodes_data = body.get("nodes", [])
    if not isinstance(nodes_data, list):
        raise HTTPException(status_code=422, detail="nodes must be a list")
    nodes: list[Node] = []
    for n in nodes_data:
        try:
            nodes.append(
                Node(
                    id=n["id"],
                    parent_id=n.get("parent_id"),
                    root_id=n.get("root_id", n.get("tree_id", n["id"])),
                    text=n["text"],
                    model=n.get("model"),
                    strategy=n.get("strategy"),
                    max_tokens=n.get("max_tokens"),
                    temperature=n.get("temperature"),
                    branch_index=n.get("branch_index"),
                    created_at=n.get("created_at", ""),
                    metadata=n.get("metadata", {}),
                    tree_id=n.get("tree_id", n.get("root_id", n["id"])),
                    kind=n.get("kind", "text"),
                    context_id=n.get("context_id"),
                    checked_out=bool(n.get("checked_out", False)),
                )
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=422, detail=f"missing field: {exc}"
            ) from exc
    return {"imported": store.import_nodes(nodes)}
