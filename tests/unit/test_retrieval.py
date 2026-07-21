from __future__ import annotations

from collections.abc import Sequence

from codemind.application.retrieval import HybridRetrievalService
from codemind.domain.models import (
    CodeChunk,
    EmbeddedChunk,
    Language,
    RankedHit,
    RepositoryRecord,
    RetrievalFilters,
    SearchHit,
    SourceType,
)
from codemind.indexing.embedding import HashEmbeddingProvider
from codemind.retrieval.analyzer import RuleQueryAnalyzer
from codemind.retrieval.fusion import (
    ContextPacker,
    HeuristicCodeReranker,
    reciprocal_rank_fusion,
)


def hit(chunk_id: str, path: str, symbol: str | None, *reasons: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=1.0,
        path=path,
        content=f"def {symbol or 'module'}(): pass",
        start_line=2,
        end_line=4,
        symbol_name=symbol,
        reasons=reasons,
        language=Language.PYTHON,
    )


def test_query_analyzer_extracts_code_features_and_filters() -> None:
    analysis = RuleQueryAnalyzer().analyze(
        "Where is `AuthService.login` calling create_token in src/auth.py? Python"
    )

    assert analysis.symbols == ("AuthService.login", "create_token")
    assert analysis.path_terms == ("src/auth.py",)
    assert analysis.filters.languages == (Language.PYTHON,)
    assert analysis.intent.value == "trace_relations"


def test_rrf_deduplicates_and_combines_reasons_deterministically() -> None:
    fused = reciprocal_rank_fusion(
        [
            [hit("a", "src/a.py", "login", "dense"), hit("b", "src/b.py", None, "dense")],
            [hit("b", "src/b.py", None, "bm25"), hit("a", "src/a.py", "login", "bm25")],
        ]
    )

    assert [item.hit.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].hit.reasons == ("dense", "bm25")


def test_context_packer_obeys_budget_and_path_diversity() -> None:
    ranked = [
        RankedHit(hit=hit(str(index), "src/a.py", f"s{index}", "bm25"), rrf_score=1 / index)
        for index in range(1, 5)
    ]
    packed = ContextPacker(token_budget=1_000, max_evidence=10, max_per_path=2).pack(
        "repo", "version", "commit", ranked
    )

    assert len(packed) == 2
    assert all(item.id.startswith("ev_") for item in packed)


class FakeStore:
    async def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        return RepositoryRecord(
            repository_id, "sample", SourceType.LOCAL, "/sample", "main", "version"
        )

    async def get_version_commit(self, version_id: str) -> str | None:
        return "commit"


class DenseIndex:
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[EmbeddedChunk]
    ) -> None:
        return None

    async def search(
        self,
        repository_id: str,
        version_id: str,
        vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        return [hit("dense", "src/dense.py", "dense_match", "dense")]


class BrokenLexicalIndex:
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[CodeChunk]
    ) -> None:
        return None

    async def search(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        raise OSError("index unavailable")


class SymbolIndex:
    async def search_symbols(
        self,
        repository_id: str,
        version_id: str,
        terms: Sequence[str],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        return [hit("symbol", "src/auth.py", "AuthService.login", "symbol_exact")]


class BrokenReranker:
    model_id = "broken"

    async def rerank(self, query: str, hits: Sequence[RankedHit]) -> list[float]:
        raise OSError("provider unavailable")


async def test_hybrid_service_degrades_failed_source_and_reranker() -> None:
    service = HybridRetrievalService(
        store=FakeStore(),  # type: ignore[arg-type]
        analyzer=RuleQueryAnalyzer(),
        embedding=HashEmbeddingProvider(64),
        vector_index=DenseIndex(),
        lexical_index=BrokenLexicalIndex(),
        symbol_index=SymbolIndex(),
        reranker=BrokenReranker(),
        context_packer=ContextPacker(),
    )

    result = await service.search("repo", "find AuthService.login")

    assert set(result.degraded_sources) == {"bm25", "reranker"}
    assert not result.reranker_applied
    assert {item.path for item in result.evidence} == {"src/auth.py", "src/dense.py"}


async def test_heuristic_reranker_prioritizes_exact_symbol() -> None:
    candidates = [
        RankedHit(hit=hit("a", "src/a.py", "other", "dense"), rrf_score=0.2),
        RankedHit(hit=hit("b", "src/auth.py", "AuthService.login", "symbol_exact"), rrf_score=0.1),
    ]
    scores = await HeuristicCodeReranker().rerank("AuthService.login", candidates)

    assert scores[1] > scores[0]
