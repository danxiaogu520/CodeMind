"""Repository and asynchronous indexing task endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from codemind.application.ports import IndexStore
from codemind.domain.models import IndexJobRecord, RepositoryRecord, SourceType

router = APIRouter()


class SourceRequest(BaseModel):
    type: Literal["local", "git"]
    path: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def validate_location(self) -> SourceRequest:
        if self.type == "local" and not self.path:
            raise ValueError("A local source requires path.")
        if self.type == "git" and not self.url:
            raise ValueError("A Git source requires url.")
        return self

    def uri(self) -> str:
        value = self.path if self.type == "local" else self.url
        if value is None:
            raise ValueError("Repository source location is missing.")
        return value


class CreateRepositoryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source: SourceRequest
    ref: str | None = Field(default=None, max_length=255)


class CreateRepositoryResponse(BaseModel):
    repository_id: str
    job_id: str
    status: str


class RepositoryResponse(BaseModel):
    id: str
    name: str
    source_type: str
    source_uri: str
    default_ref: str | None
    active_index_version_id: str | None


class CreateIndexJobRequest(BaseModel):
    ref: str | None = Field(default=None, max_length=255)


class IndexJobResponse(BaseModel):
    id: str
    repository_id: str
    status: str
    stage: str
    target_ref: str | None
    progress: dict[str, int]
    warnings: int
    error_summary: str | None
    delta: dict[str, int]


def _store(request: Request) -> IndexStore:
    return request.app.state.index_store


@router.post(
    "/repositories",
    response_model=CreateRepositoryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["repositories"],
)
async def create_repository(
    payload: CreateRepositoryRequest, request: Request
) -> CreateRepositoryResponse:
    repository, job = await _store(request).create_repository(
        payload.name,
        SourceType(payload.source.type),
        payload.source.uri(),
        payload.ref,
    )
    return CreateRepositoryResponse(
        repository_id=repository.id, job_id=job.id, status=job.status.value
    )


@router.get(
    "/repositories/{repository_id}",
    response_model=RepositoryResponse,
    tags=["repositories"],
)
async def get_repository(repository_id: str, request: Request) -> RepositoryResponse:
    repository = await _store(request).get_repository(repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return _repository_response(repository)


@router.post(
    "/repositories/{repository_id}/index-jobs",
    response_model=IndexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["indexing"],
)
async def create_index_job(
    repository_id: str, payload: CreateIndexJobRequest, request: Request
) -> IndexJobResponse:
    store = _store(request)
    if await store.get_repository(repository_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    job = await store.enqueue_job(repository_id, payload.ref)
    return _job_response(job)


@router.get("/index-jobs/{job_id}", response_model=IndexJobResponse, tags=["indexing"])
async def get_index_job(job_id: str, request: Request) -> IndexJobResponse:
    job = await _store(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Index job not found.")
    return _job_response(job)


def _repository_response(repository: RepositoryRecord) -> RepositoryResponse:
    return RepositoryResponse(
        id=repository.id,
        name=repository.name,
        source_type=repository.source_type.value,
        source_uri=repository.source_uri,
        default_ref=repository.default_ref,
        active_index_version_id=repository.active_index_version_id,
    )


def _job_response(job: IndexJobRecord) -> IndexJobResponse:
    return IndexJobResponse(
        id=job.id,
        repository_id=job.repository_id,
        status=job.status.value,
        stage=job.stage,
        target_ref=job.target_ref,
        progress={"processed": job.processed, "total": job.total},
        warnings=job.warnings,
        error_summary=job.error_summary,
        delta={
            "files_added": job.delta.files_added,
            "files_changed": job.delta.files_changed,
            "files_deleted": job.delta.files_deleted,
            "files_reused": job.delta.files_reused,
            "chunks_embedded": job.delta.chunks_embedded,
            "chunks_reused": job.delta.chunks_reused,
        },
    )
