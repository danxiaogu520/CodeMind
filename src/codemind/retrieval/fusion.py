"""Rank fusion, reranking baseline, and evidence packing."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence

from codemind.domain.models import Evidence, RankedHit, SearchHit
from codemind.retrieval.tokenizer import tokenize_code


def reciprocal_rank_fusion(
    result_sets: Sequence[Sequence[SearchHit]], *, rrf_k: int = 60
) -> list[RankedHit]:
    scores: dict[str, float] = {}
    hits: dict[str, SearchHit] = {}
    reasons: dict[str, list[str]] = {}
    for result_set in result_sets:
        for rank, hit in enumerate(result_set, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            hits.setdefault(hit.chunk_id, hit)
            merged = reasons.setdefault(hit.chunk_id, [])
            for reason in hit.reasons:
                if reason not in merged:
                    merged.append(reason)
    ranked = [
        RankedHit(
            hit=SearchHit(
                chunk_id=hit.chunk_id,
                score=hit.score,
                path=hit.path,
                content=hit.content,
                start_line=hit.start_line,
                end_line=hit.end_line,
                symbol_name=hit.symbol_name,
                reasons=tuple(reasons[chunk_id]),
                language=hit.language,
            ),
            rrf_score=scores[chunk_id],
        )
        for chunk_id, hit in hits.items()
    ]
    return sorted(ranked, key=lambda item: (-item.rrf_score, item.hit.chunk_id))


class HeuristicCodeReranker:
    """Offline reranker baseline; replaceable through the application port."""

    model_id = "heuristic-code-reranker-v1"

    async def rerank(self, query: str, hits: Sequence[RankedHit]) -> list[float]:
        query_terms = set(tokenize_code(query))
        scores: list[float] = []
        for ranked in hits:
            hit = ranked.hit
            searchable = " ".join((hit.path, hit.symbol_name or "", hit.content))
            document_terms = Counter(tokenize_code(searchable))
            overlap = sum(
                1.0 + min(document_terms[term], 3) * 0.15
                for term in query_terms
                if term in document_terms
            )
            exact_symbol = (
                2.0 if hit.symbol_name and hit.symbol_name.lower() in query.lower() else 0.0
            )
            reason_bonus = 0.25 * len(hit.reasons)
            scores.append(overlap + exact_symbol + reason_bonus + ranked.rrf_score)
        return scores


class ContextPacker:
    def __init__(
        self, *, token_budget: int = 6_000, max_evidence: int = 10, max_per_path: int = 3
    ) -> None:
        if token_budget <= 0 or max_evidence <= 0 or max_per_path <= 0:
            raise ValueError("Context packing limits must be positive.")
        self._token_budget = token_budget
        self._max_evidence = max_evidence
        self._max_per_path = max_per_path

    def pack(
        self,
        repository_id: str,
        version_id: str,
        commit_sha: str,
        hits: Sequence[RankedHit],
        limit: int | None = None,
    ) -> tuple[Evidence, ...]:
        used_tokens = 0
        path_counts: Counter[str] = Counter()
        evidence: list[Evidence] = []
        for ranked in hits:
            hit = ranked.hit
            estimated_tokens = max(1, (len(hit.content) + 3) // 4)
            if path_counts[hit.path] >= self._max_per_path:
                continue
            if used_tokens + estimated_tokens > self._token_budget:
                continue
            digest = hashlib.sha256(f"{version_id}:{hit.chunk_id}".encode()).hexdigest()[:12]
            evidence.append(
                Evidence(
                    id=f"ev_{digest}",
                    repository_id=repository_id,
                    index_version_id=version_id,
                    commit_sha=commit_sha,
                    path=hit.path,
                    language=hit.language,
                    symbol=hit.symbol_name,
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                    content=hit.content,
                    rrf_score=ranked.rrf_score,
                    rerank_score=ranked.rerank_score,
                    reasons=hit.reasons,
                )
            )
            used_tokens += estimated_tokens
            path_counts[hit.path] += 1
            if len(evidence) >= min(limit or self._max_evidence, self._max_evidence):
                break
        return tuple(evidence)
