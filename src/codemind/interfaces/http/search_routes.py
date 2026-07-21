"""Hybrid code search HTTP endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from codemind.application.retrieval import (
    HybridRetrievalService,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
    RetrievalUnavailableError,
)
from codemind.domain.models import Evidence, Language, QueryAnalysis, RetrievalFilters

router = APIRouter()


class SearchFiltersRequest(BaseModel):
    languages: tuple[Language, ...] = ()
    path_prefix: str | None = Field(default=None, max_length=500)

    @field_validator("path_prefix")
    @classmethod
    def validate_path_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("path_prefix must be a safe repository-relative path.")
        return normalized

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: tuple[Language, ...]) -> tuple[Language, ...]:
        if len(value) > 4:
            raise ValueError("At most four languages can be selected.")
        return tuple(dict.fromkeys(value))


class SearchRequest(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    filters: SearchFiltersRequest = Field(default_factory=SearchFiltersRequest)
    limit: int = Field(default=10, ge=1, le=50)
    include_content: bool = True


class QueryAnalysisResponse(BaseModel):
    intent: str
    semantic_query: str
    keywords: list[str]
    symbols: list[str]
    path_terms: list[str]
    languages: list[str]
    path_prefix: str | None


class EvidenceScoresResponse(BaseModel):
    rrf: float
    rerank: float | None


class EvidenceResponse(BaseModel):
    evidence_id: str
    repository_id: str
    index_version_id: str
    commit_sha: str
    path: str
    language: str | None
    symbol: str | None
    start_line: int
    end_line: int
    content: str | None
    scores: EvidenceScoresResponse
    reasons: list[str]


class SearchDiagnosticsResponse(BaseModel):
    degraded_sources: list[str]
    reranker_applied: bool
    timings_ms: dict[str, float]


class SearchResponse(BaseModel):
    repository_id: str
    index_version_id: str
    query_analysis: QueryAnalysisResponse
    evidence: list[EvidenceResponse]
    diagnostics: SearchDiagnosticsResponse


def _service(request: Request) -> HybridRetrievalService:
    return request.app.state.retrieval_service


@router.post(
    "/repositories/{repository_id}/search",
    response_model=SearchResponse,
    tags=["retrieval"],
)
async def search_repository(
    repository_id: str, payload: SearchRequest, request: Request, response: Response
) -> SearchResponse:
    filters = RetrievalFilters(
        languages=tuple(payload.filters.languages),
        path_prefix=payload.filters.path_prefix,
    )
    try:
        result = await _service(request).search(
            repository_id, payload.query, filters=filters, limit=payload.limit
        )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Repository not found.") from exc
    except RepositoryNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository index is not ready.",
        ) from exc
    except RetrievalUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="All retrieval sources are unavailable.",
        ) from exc
    response.headers["X-CodeMind-Index-Version"] = result.index_version_id
    return SearchResponse(
        repository_id=result.repository_id,
        index_version_id=result.index_version_id,
        query_analysis=_analysis_response(result.query),
        evidence=[_evidence_response(item, payload.include_content) for item in result.evidence],
        diagnostics=SearchDiagnosticsResponse(
            degraded_sources=list(result.degraded_sources),
            reranker_applied=result.reranker_applied,
            timings_ms=result.timings_ms,
        ),
    )


def _analysis_response(analysis: QueryAnalysis) -> QueryAnalysisResponse:
    return QueryAnalysisResponse(
        intent=analysis.intent.value,
        semantic_query=analysis.semantic_query,
        keywords=list(analysis.keywords),
        symbols=list(analysis.symbols),
        path_terms=list(analysis.path_terms),
        languages=[language.value for language in analysis.filters.languages],
        path_prefix=analysis.filters.path_prefix,
    )


def _evidence_response(evidence: Evidence, include_content: bool) -> EvidenceResponse:
    return EvidenceResponse(
        evidence_id=evidence.id,
        repository_id=evidence.repository_id,
        index_version_id=evidence.index_version_id,
        commit_sha=evidence.commit_sha,
        path=evidence.path,
        language=evidence.language.value if evidence.language else None,
        symbol=evidence.symbol,
        start_line=evidence.start_line,
        end_line=evidence.end_line,
        content=evidence.content if include_content else None,
        scores=EvidenceScoresResponse(rrf=evidence.rrf_score, rerank=evidence.rerank_score),
        reasons=list(evidence.reasons),
    )
