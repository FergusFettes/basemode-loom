from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictInt

from ..catalog import FACETS, TreeCatalogEntry, build_tree_catalog
from ..config import Config, config_to_dict
from ..credentials import (
    MAX_KEY_BYTES,
    ProviderStatus,
    is_known_provider,
    list_provider_status,
    store_provider_key,
)
from ..graph_stats import analyze_subtree
from ..images import MAX_PROMPT_CHARS, ImageGenerationError, generate_branch_image
from ..model_ratings import get_rating, is_valid_rating, list_ratings, set_rating
from ..retrieval import embed_corpus, get_backend, get_embedder, vector_count
from ..retrieval.vectors import read_meta
from ..stats import analyze_tree
from ..store import GenerationStore, Node
from ._security import value_exceeds_field_limit
from ._serialize import node_to_dict, tree_to_dict

router = APIRouter(prefix="/api")


def _get_store(request: Request) -> GenerationStore:
    return request.app.state.store_resolver(request.scope)


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
        "archived": tree.archived,
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


class EmbeddingBuildRequest(BaseModel):
    model: str = Field(
        default="hash", description="'hash', 'mlx', or an MLX/Hugging Face model ID"
    )
    dim: int = Field(default=256, ge=1, description="Hash embedder vector dimension")
    min_chars: int = Field(default=1, ge=0)
    batch_size: int = Field(default=64, ge=1)
    incremental: bool = False


class EmbeddingStatusResponse(BaseModel):
    available: bool
    model: str | None = None
    dim: int | None = None
    vectors: int = 0


class EmbeddingBuildResponse(EmbeddingStatusResponse):
    indexed: int
    incremental: bool


class CredentialStatus(BaseModel):
    """A provider's key state. Deliberately has no field for the key itself."""

    provider: str
    env_var: str
    configured: bool
    masked: str | None = Field(
        default=None, description="Elided preview, e.g. 'sk-a...wxyz'. Never the key."
    )
    source: str | None = Field(
        default=None, description="'stored' (this server's key file) or 'environment'"
    )


class CredentialListResponse(BaseModel):
    providers: list[CredentialStatus]
    writable: bool = Field(description="Whether this server accepts key writes")


class StoreCredentialBody(BaseModel):
    value: str = Field(description="The provider API key. Write-only.")


def _credential_status(status: ProviderStatus) -> CredentialStatus:
    return CredentialStatus(
        provider=status.provider,
        env_var=status.env_var,
        configured=status.configured,
        masked=status.masked,
        source=status.source,
    )


@router.get("/keys", response_model=CredentialListResponse)
def list_credentials(request: Request) -> CredentialListResponse:
    """Report which providers have a key configured.

    There is no endpoint that returns a stored key, by design — this is the
    only read surface, and it is masked.
    """
    return CredentialListResponse(
        providers=[_credential_status(status) for status in list_provider_status()],
        writable=_get_config(request).server.credential_writes_enabled(),
    )


@router.put("/keys/{provider}", response_model=CredentialStatus)
def store_credential(
    provider: str, body: StoreCredentialBody, request: Request
) -> CredentialStatus:
    """Store a provider API key. The key is write-only once submitted.

    The key travels in the request body rather than the path or a query
    parameter so that it stays out of access logs and browser history.
    """
    if not _get_config(request).server.credential_writes_enabled():
        raise HTTPException(
            status_code=403, detail={"code": "credential_writes_disabled"}
        )
    if not is_known_provider(provider):
        raise HTTPException(status_code=404, detail={"code": "unknown_provider"})

    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail={"code": "empty_key"})
    if len(value.encode("utf-8")) > MAX_KEY_BYTES:
        raise HTTPException(status_code=413, detail={"code": "key_too_large"})

    return _credential_status(store_provider_key(provider, value))


@router.get("/roots")
def list_roots(
    store: StoreDep,
    archived: Annotated[
        bool, Query(description="Show archived trees instead of active ones")
    ] = False,
) -> list[dict]:
    return [_root_summary(store, r) for r in store.roots(archived=archived)]


@router.get("/embeddings", response_model=EmbeddingStatusResponse)
def embedding_status(store: StoreDep) -> EmbeddingStatusResponse:
    """Describe the semantic index stored in the active corpus database."""
    with store.connect() as conn:
        meta = read_meta(conn)
    return EmbeddingStatusResponse(
        available=meta is not None,
        model=meta[0] if meta else None,
        dim=meta[1] if meta else None,
        vectors=vector_count(store.db_path) if meta else 0,
    )


@router.post("/embeddings", response_model=EmbeddingBuildResponse)
def build_embeddings(
    body: EmbeddingBuildRequest, store: StoreDep
) -> EmbeddingBuildResponse:
    """Build or incrementally update the semantic index for the active corpus."""
    try:
        embedder = get_embedder(body.model, dim=body.dim)
        indexed = embed_corpus(
            store.db_path,
            embedder,
            min_chars=body.min_chars,
            batch_size=body.batch_size,
            incremental=body.incremental,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail={"code": "embedding_build_failed"}
        ) from exc
    return EmbeddingBuildResponse(
        available=True,
        model=embedder.name,
        dim=embedder.dim,
        vectors=vector_count(store.db_path),
        indexed=indexed,
        incremental=body.incremental,
    )


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
            ranking = {hit.tree_id: (hit.score, hit.best_node_id) for hit in hits}
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
            for value, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
    return result


@router.post("/roots", status_code=201)
def create_root(body: CreateRootBody, store: StoreDep, request: Request) -> dict:
    # The total body is bounded by middleware; this separately prevents a
    # single persisted value from consuming the whole allowance.
    request_values = body.model_dump()
    if value_exceeds_field_limit(
        request_values, request.app.state.config.server.max_field_bytes
    ):
        raise HTTPException(status_code=413, detail={"code": "field_too_large"})
    meta: dict[str, Any] = {}
    for key in ("name", "model", "max_tokens", "n_branches", "context"):
        val = getattr(body, key)
        if val is not None:
            meta[key] = val
    if body.temperature is not None:
        meta["temperature"] = body.temperature
    root = store.create_root(body.text, metadata=meta)
    return _root_summary(store, root)


class UpdateRootBody(BaseModel):
    archived: bool


@router.patch("/roots/{root_id}")
def update_root(root_id: str, body: UpdateRootBody, store: StoreDep) -> dict:
    root = store.get(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="root not found")
    store.set_tree_archived(root.tree_id, body.archived)
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


class GenerateImageResponse(BaseModel):
    image_base64: str
    mime_type: str
    prompt: str


@router.post("/roots/{root_id}/image", response_model=GenerateImageResponse)
def generate_root_image(root_id: str, store: StoreDep) -> GenerateImageResponse:
    if store.get(root_id) is None:
        raise HTTPException(status_code=404, detail="root not found")
    tree = store.tree_for_node(root_id)
    node_id = tree.current_node_id or root_id
    full_text = store.full_text(node_id)
    prompt = full_text[-MAX_PROMPT_CHARS:]
    try:
        image_base64, mime_type = generate_branch_image(prompt)
    except ImageGenerationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "image_generation_failed", "message": str(exc)},
        ) from exc
    return GenerateImageResponse(
        image_base64=image_base64, mime_type=mime_type, prompt=prompt
    )


@router.get("/nodes/{node_id}")
def get_node(node_id: str, store: StoreDep) -> dict:
    node = store.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return {**node_to_dict(node), "full_text": store.full_text(node_id)}


@router.get("/nodes/{node_id}/shape")
def get_node_shape(node_id: str, store: StoreDep) -> dict:
    """Topology metrics (size, depth, branching) for the subtree at `node_id`."""
    if store.get(node_id) is None:
        raise HTTPException(status_code=404, detail="node not found")
    return analyze_subtree(store, node_id).as_dict()


@router.get("/models")
def list_models(
    provider: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    available: Annotated[bool, Query()] = True,
    verified: Annotated[bool, Query()] = False,
    since: Annotated[str | None, Query()] = None,
) -> dict:
    try:
        import basemode.models as bm  # type: ignore[import]

        if since:
            try:
                bm.parse_since(since)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "invalid_since", "message": str(exc)},
                ) from exc

        picker = getattr(bm, "list_model_picker_entries", None)
        if callable(picker):
            return {
                "models": picker(
                    provider=provider,
                    search=search,
                    available_only=available,
                    verified_only=verified,
                    since=since,
                )
            }
        return {"models": bm.list_models(available_only=available)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "model_discovery_failed"},
        ) from exc


class ModelRatingBody(BaseModel):
    model: str
    # Strict, so JSON `true` is rejected rather than coerced to a thumbs up.
    rating: StrictInt | None = Field(
        default=None,
        description="1 for thumbs up, -1 for thumbs down, null to clear.",
    )


class ModelRating(BaseModel):
    model: str
    rating: int | None


class ModelRatingListResponse(BaseModel):
    ratings: dict[str, int]
    writable: bool


@router.get("/models/ratings", response_model=ModelRatingListResponse)
def list_model_ratings(request: Request) -> ModelRatingListResponse:
    """Every model this user has rated, keyed by resolved model ID."""
    return ModelRatingListResponse(
        ratings=list_ratings(),
        writable=_get_config(request).server.rating_writes_enabled(),
    )


@router.put("/models/rating", response_model=ModelRating)
def store_model_rating(body: ModelRatingBody, request: Request) -> ModelRating:
    """Rate a model up or down, or clear its rating with `null`.

    The model ID travels in the body rather than the path because model IDs
    contain slashes (`anthropic/claude-opus-5`), which a path parameter would
    force every caller to encode. The rating reorders `GET /api/models`; it
    changes nothing about generation.
    """
    if not _get_config(request).server.rating_writes_enabled():
        raise HTTPException(status_code=403, detail={"code": "rating_writes_disabled"})
    model = body.model.strip()
    if not model:
        raise HTTPException(status_code=422, detail={"code": "empty_model"})
    if not is_valid_rating(body.rating):
        raise HTTPException(status_code=422, detail={"code": "invalid_rating"})

    resolved, rating = set_rating(model, body.rating)
    return ModelRating(model=resolved, rating=rating)


@router.get("/models/rating", response_model=ModelRating)
def read_model_rating(
    model: Annotated[str, Query(description="Model ID to look up")],
) -> ModelRating:
    """This user's thumb for one model, without listing every rating."""
    resolved = model.strip()
    if not resolved:
        raise HTTPException(status_code=422, detail={"code": "empty_model"})
    return ModelRating(model=resolved, rating=get_rating(resolved))


@router.post("/import", status_code=201)
def import_tree(body: dict, store: StoreDep, request: Request) -> dict:
    if value_exceeds_field_limit(body, request.app.state.config.server.max_field_bytes):
        raise HTTPException(status_code=413, detail={"code": "field_too_large"})
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
