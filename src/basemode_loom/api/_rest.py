from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..catalog import FACETS, TreeCatalogEntry, build_tree_catalog
from ..config import Config, config_to_dict
from ..retrieval import get_backend
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


class FacetValue(BaseModel):
    value: str
    count: int


class SearchCapabilities(BaseModel):
    id: bool = True
    metadata: bool = True
    keyword: bool
    semantic: bool
    message: str = ""


class TreeSummary(BaseModel):
    id: str = Field(description="Root node ID")
    tree_id: str
    name: str | None
    created_at: str
    node_count: int
    root_preview: str
    leaf_preview: str
    category: str
    domain: str
    sources: list[str]
    models: list[str]
    score: float | None = None
    best_node_id: str | None = None


class TreeCatalogResponse(BaseModel):
    items: list[TreeSummary]
    total: int = Field(description="Number of matches before pagination")
    offset: int
    limit: int
    facets: dict[str, list[FacetValue]]
    search: SearchCapabilities


@router.get("/roots")
def list_roots(store: StoreDep) -> list[dict]:
    return [_root_summary(store, r) for r in store.roots()]


TreeSort = Literal["auto", "relevance", "recent", "oldest", "nodes", "name"]


@router.get("/trees", response_model=TreeCatalogResponse)
def list_tree_catalog(
    store: StoreDep,
    q: Annotated[
        str | None,
        Query(description="ID, keyword, semantic, or metadata query", max_length=500),
    ] = None,
    category: Annotated[list[str] | None, Query()] = None,
    domain: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    sort: TreeSort = "auto",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TreeCatalogResponse:
    """List picker-ready trees with facets, hybrid search, and pagination.

    Repeat a facet parameter to select multiple values. Values within one facet
    are ORed; different facets are ANDed.
    """
    entries = build_tree_catalog(store)
    backend = get_backend(store)
    status = backend.status()
    query = (q or "").strip()
    ranking: dict[str, tuple[float, str]] | None = None
    metadata_query = ""
    if query:
        hits = backend.search(query, limit=max(len(entries), 1))
        if hits or status.keyword or status.semantic:
            ranking = {
                hit.tree_id: (hit.score, hit.best_node_id) for hit in hits
            }
        else:
            metadata_query = query.lower()

    selected = {
        "category": set(category or []),
        "domain": set(domain or []),
        "source": set(source or []),
        "model": set(model or []),
    }
    filtered = [
        entry
        for entry in entries
        if _entry_matches(entry, selected, metadata_query, ranking)
    ]
    resolved_sort = "relevance" if sort == "auto" and ranking is not None else sort
    if resolved_sort == "auto":
        resolved_sort = "recent"
    _sort_catalog(filtered, resolved_sort, ranking)

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return TreeCatalogResponse(
        items=[_tree_summary(entry, ranking) for entry in page],
        total=total,
        offset=offset,
        limit=limit,
        facets=_facet_counts(entries),
        search=SearchCapabilities(
            keyword=status.keyword,
            semantic=status.semantic,
            message=status.message,
        ),
    )


def _entry_matches(
    entry: TreeCatalogEntry,
    selected: dict[str, set[str]],
    metadata_query: str,
    ranking: dict[str, tuple[float, str]] | None,
) -> bool:
    for facet, values in selected.items():
        if values and not values.intersection(entry.facet_values(facet)):
            return False
    if ranking is not None and entry.root.tree_id not in ranking:
        return False
    if metadata_query:
        haystack = " ".join(
            (
                entry.name or "",
                entry.category,
                entry.domain,
                entry.source,
                entry.players,
                entry.root.id,
            )
        ).lower()
        if metadata_query not in haystack:
            return False
    return True


def _sort_catalog(
    entries: list[TreeCatalogEntry],
    sort: str,
    ranking: dict[str, tuple[float, str]] | None,
) -> None:
    if sort == "relevance":
        entries.sort(
            key=lambda entry: (ranking or {}).get(entry.root.tree_id, (0.0, ""))[0],
            reverse=True,
        )
    elif sort == "oldest":
        entries.sort(key=lambda entry: (entry.root.created_at, entry.root.id))
    elif sort == "nodes":
        entries.sort(
            key=lambda entry: (entry.node_count, entry.root.created_at), reverse=True
        )
    elif sort == "name":
        entries.sort(key=lambda entry: (entry.name or entry.root.id).lower())
    else:
        entries.sort(
            key=lambda entry: (entry.root.created_at, entry.root.id), reverse=True
        )


def _tree_summary(
    entry: TreeCatalogEntry,
    ranking: dict[str, tuple[float, str]] | None,
) -> TreeSummary:
    match = ranking.get(entry.root.tree_id) if ranking is not None else None
    return TreeSummary(
        id=entry.root.id,
        tree_id=entry.root.tree_id,
        name=entry.name,
        created_at=entry.root.created_at,
        node_count=entry.node_count,
        root_preview=entry.root_preview,
        leaf_preview=entry.leaf_preview,
        category=entry.category,
        domain=entry.domain,
        sources=list(entry.sources),
        models=list(entry.models),
        score=match[0] if match else None,
        best_node_id=match[1] if match else None,
    )


def _facet_counts(entries: list[TreeCatalogEntry]) -> dict[str, list[FacetValue]]:
    result: dict[str, list[FacetValue]] = {}
    for facet in FACETS:
        counts = Counter(
            value for entry in entries for value in entry.facet_values(facet)
        )
        result[facet] = [
            FacetValue(value=value, count=count)
            for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    return result


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
                    text=n["text"],
                    model=n.get("model"),
                    strategy=n.get("strategy"),
                    max_tokens=n.get("max_tokens"),
                    temperature=n.get("temperature"),
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
