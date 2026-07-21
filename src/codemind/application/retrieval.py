"""Hybrid retrieval application service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from time import perf_counter

import structlog

from codemind.application.ports import (
    EmbeddingProvider,
    IndexStore,
    LexicalIndex,
    QueryAnalyzer,
    QueryEmbeddingProvider,
    RerankerProvider,
    SymbolIndex,
    VectorIndex,
)
from codemind.domain.models import (
    RankedHit,
    RetrievalFilters,
    RetrievalResult,
    SearchHit,
)
from codemind.retrieval.fusion import ContextPacker, reciprocal_rank_fusion

logger = structlog.get_logger(__name__)


class RepositoryNotFoundError(LookupError):
    pass


class RepositoryNotReadyError(RuntimeError):
    pass


class RetrievalUnavailableError(RuntimeError):
    pass


class HybridRetrievalService:
    def __init__(
        self,
        *,
        store: IndexStore,
        analyzer: QueryAnalyzer,
        embedding: EmbeddingProvider,
        vector_index: VectorIndex,
        lexical_index: LexicalIndex,
        symbol_index: SymbolIndex,
        reranker: RerankerProvider,
        context_packer: ContextPacker,
        recall_limit: int = 40,
        rerank_limit: int = 30,
        rerank_timeout_seconds: float = 2.0,
    ) -> None:
        self._store = store
        self._analyzer = analyzer
        self._embedding = embedding
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._symbol_index = symbol_index
        self._reranker = reranker
        self._context_packer = context_packer
        self._recall_limit = recall_limit
        self._rerank_limit = rerank_limit
        self._rerank_timeout_seconds = rerank_timeout_seconds

    async def search(
        self,
        repository_id: str,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 10,
    ) -> RetrievalResult:
        repository = await self._store.get_repository(repository_id)
        if repository is None:
            raise RepositoryNotFoundError(repository_id)
        version_id = repository.active_index_version_id
        if version_id is None:
            raise RepositoryNotReadyError(repository_id)
        return await self.search_version(
            repository_id, version_id, query, filters=filters, limit=limit
        )

    async def search_version(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 10,
    ) -> RetrievalResult:
        started = perf_counter()
        commit_sha = await self._store.get_version_commit(version_id)
        if commit_sha is None:
            raise RepositoryNotReadyError(repository_id)

        analysis = self._analyzer.analyze(query, filters)
        timings: dict[str, float] = {}
        retrievals = await asyncio.gather(
            self._timed(
                "dense",
                self._dense(repository_id, version_id, analysis.semantic_query, analysis.filters),
                timings,
            ),
            self._timed(
                "bm25",
                self._lexical_index.search(
                    repository_id,
                    version_id,
                    analysis.semantic_query,
                    self._recall_limit,
                    analysis.filters,
                ),
                timings,
            ),
            self._timed(
                "symbol",
                self._symbol_index.search_symbols(
                    repository_id,
                    version_id,
                    (*analysis.symbols, *analysis.path_terms, *analysis.keywords),
                    self._recall_limit,
                    analysis.filters,
                ),
                timings,
            ),
        )
        degraded = tuple(name for name, _, error in retrievals if error is not None)
        successful = [hits for _, hits, error in retrievals if error is None]
        if not successful:
            raise RetrievalUnavailableError("All retrieval sources failed.")
        fused = reciprocal_rank_fusion(successful)
        candidates = fused[: self._rerank_limit]
        reranker_applied = False
        if candidates:
            rerank_started = perf_counter()
            try:
                scores = await asyncio.wait_for(
                    self._reranker.rerank(analysis.semantic_query, candidates),
                    timeout=self._rerank_timeout_seconds,
                )
                if len(scores) != len(candidates):
                    raise ValueError("Reranker returned an invalid score count.")
                candidates = sorted(
                    (
                        RankedHit(
                            hit=candidate.hit,
                            rrf_score=candidate.rrf_score,
                            rerank_score=score,
                        )
                        for candidate, score in zip(candidates, scores, strict=True)
                    ),
                    key=lambda item: (
                        -(item.rerank_score or 0.0),
                        -item.rrf_score,
                        item.hit.chunk_id,
                    ),
                )
                reranker_applied = True
            except Exception:
                degraded = (*degraded, "reranker")
                await logger.awarning(
                    "reranker_degraded",
                    repository_id=repository_id,
                    model=self._reranker.model_id,
                )
            timings["rerank"] = round((perf_counter() - rerank_started) * 1000, 3)

        evidence = self._context_packer.pack(
            repository_id, version_id, commit_sha, candidates, limit=limit
        )
        timings["total"] = round((perf_counter() - started) * 1000, 3)
        return RetrievalResult(
            repository_id=repository_id,
            index_version_id=version_id,
            query=analysis,
            evidence=evidence,
            degraded_sources=degraded,
            reranker_applied=reranker_applied,
            timings_ms=timings,
        )

    async def _dense(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        filters: RetrievalFilters,
    ) -> list[SearchHit]:
        if isinstance(self._embedding, QueryEmbeddingProvider):
            vector = await self._embedding.embed_query(query)
        else:
            vector = (await self._embedding.embed([query]))[0]
        return await self._vector_index.search(
            repository_id, version_id, vector, self._recall_limit, filters
        )

    @staticmethod
    async def _timed(
        name: str,
        operation: Awaitable[list[SearchHit]],
        timings: dict[str, float],
    ) -> tuple[str, list[SearchHit], Exception | None]:
        started = perf_counter()
        try:
            return name, await operation, None
        except Exception as exc:
            await logger.awarning(
                "retrieval_source_degraded", source=name, error_type=type(exc).__name__
            )
            return name, [], exc
        finally:
            timings[name] = round((perf_counter() - started) * 1000, 3)
