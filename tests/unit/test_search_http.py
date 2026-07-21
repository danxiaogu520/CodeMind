from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from codemind.config import Settings
from codemind.domain.models import (
    Evidence,
    Language,
    QueryAnalysis,
    QueryIntent,
    RetrievalFilters,
    RetrievalResult,
)
from codemind.interfaces.http.app import create_app


class FakeRetrievalService:
    async def search(
        self,
        repository_id: str,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 10,
    ) -> RetrievalResult:
        analysis = QueryAnalysis(
            original_query=query,
            semantic_query=query,
            intent=QueryIntent.LOCATE_SYMBOL,
            keywords=("login",),
            symbols=("AuthService.login",),
            path_terms=(),
            filters=filters or RetrievalFilters(),
        )
        evidence = Evidence(
            id="ev_test",
            repository_id=repository_id,
            index_version_id="version-1",
            commit_sha="abc123",
            path="src/auth.py",
            language=Language.PYTHON,
            symbol="AuthService.login",
            start_line=3,
            end_line=5,
            content="def login(): pass",
            rrf_score=0.03,
            rerank_score=2.0,
            reasons=("symbol_exact",),
        )
        return RetrievalResult(
            repository_id=repository_id,
            index_version_id="version-1",
            query=analysis,
            evidence=(evidence,)[:limit],
            degraded_sources=(),
            reranker_applied=True,
            timings_ms={"total": 1.0},
        )


@asynccontextmanager
async def search_client() -> AsyncGenerator[AsyncClient]:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://user:pass@127.0.0.1:1/test",
        qdrant_url="http://127.0.0.1:1",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.retrieval_service = FakeRetrievalService()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_search_api_returns_evidence_contract() -> None:
    async with search_client() as client:
        response = await client.post(
            "/api/v1/repositories/repo/search",
            json={"query": "find AuthService.login", "include_content": False},
        )

    assert response.status_code == 200
    assert response.headers["X-CodeMind-Index-Version"] == "version-1"
    assert response.json()["evidence"][0] == {
        "evidence_id": "ev_test",
        "repository_id": "repo",
        "index_version_id": "version-1",
        "commit_sha": "abc123",
        "path": "src/auth.py",
        "language": "python",
        "symbol": "AuthService.login",
        "start_line": 3,
        "end_line": 5,
        "content": None,
        "scores": {"rrf": 0.03, "rerank": 2.0},
        "reasons": ["symbol_exact"],
    }


async def test_search_api_rejects_unsafe_path_prefix(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/repositories/repo/search",
        json={"query": "login", "filters": {"path_prefix": "../secrets"}},
    )

    assert response.status_code == 422
