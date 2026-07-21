"""Read-only APIs for inspecting version-bound hierarchical memory."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from codemind.application.ports import IndexStore, MemoryPort
from codemind.domain.models import MemoryLayer, MemoryRecord

router = APIRouter()


class MemoryResponse(BaseModel):
    id: str
    index_version_id: str
    layer: str
    scope_key: str
    content: str
    session_id: str | None
    run_id: str | None
    evidence_ids: list[str]
    source_paths: list[str]
    source_hashes: list[str]
    stale: bool
    confidence: float


class MemoryListResponse(BaseModel):
    repository_id: str
    index_version_id: str
    items: list[MemoryResponse]


@router.get(
    "/repositories/{repository_id}/memories",
    response_model=MemoryListResponse,
    tags=["memory"],
)
async def list_memories(
    repository_id: str,
    request: Request,
    query: str = Query(default="", max_length=4000),
    session_id: str | None = Query(default=None, max_length=100),
    layer: MemoryLayer | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> MemoryListResponse:
    index_store: IndexStore = request.app.state.index_store
    memory: MemoryPort = request.app.state.memory_store
    repository = await index_store.get_repository(repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    if repository.active_index_version_id is None:
        raise HTTPException(status_code=409, detail="Repository index is not ready.")
    memories = await memory.recall(
        repository_id,
        repository.active_index_version_id,
        query,
        session_id,
        limit=100 if layer is not None else limit,
    )
    if layer is not None:
        memories = [item for item in memories if item.layer is layer][:limit]
    return MemoryListResponse(
        repository_id=repository_id,
        index_version_id=repository.active_index_version_id,
        items=[_response(item) for item in memories],
    )


def _response(memory: MemoryRecord) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        index_version_id=memory.index_version_id,
        layer=memory.layer.value,
        scope_key=memory.scope_key,
        content=memory.content,
        session_id=memory.session_id,
        run_id=memory.run_id,
        evidence_ids=list(memory.evidence_ids),
        source_paths=list(memory.source_paths),
        source_hashes=list(memory.source_hashes),
        stale=memory.stale,
        confidence=memory.confidence,
    )
