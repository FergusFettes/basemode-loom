from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..stats import analyze_tree
from ..store import GenerationStore, Node
from ._serialize import node_to_dict

router = APIRouter(prefix="/api")


def _get_store(request: Request) -> GenerationStore:
    return request.app.state.store


StoreDep = Annotated[GenerationStore, Depends(_get_store)]


def _root_summary(store: GenerationStore, root: Node) -> dict[str, Any]:
    return {
        "id": root.id,
        "text": root.text[:200],
        "name": root.metadata.get("name"),
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
    return {"nodes": [node_to_dict(n) for n in store.tree(root_id)]}


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
        from basemode.models import list_models as _list_models  # type: ignore[import]

        return {"models": _list_models(available_only=True)}
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
                    root_id=n["root_id"],
                    text=n["text"],
                    model=n.get("model"),
                    strategy=n.get("strategy"),
                    max_tokens=n.get("max_tokens"),
                    temperature=n.get("temperature"),
                    branch_index=n.get("branch_index"),
                    created_at=n.get("created_at", ""),
                    metadata=n.get("metadata", {}),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc}") from exc
    return {"imported": store.import_nodes(nodes)}
