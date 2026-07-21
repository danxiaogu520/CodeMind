"""Deterministic code-aware query analysis baseline."""

from __future__ import annotations

import re

from codemind.domain.models import (
    Language,
    QueryAnalysis,
    QueryIntent,
    RetrievalFilters,
)
from codemind.retrieval.tokenizer import tokenize_code

QUOTED = re.compile(r"[`'\"]([^`'\"]+)[`'\"]")
PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
IDENTIFIER = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\b")
SYMBOL_SHAPE = re.compile(
    r"(?:[A-Z][A-Za-z0-9]*|[a-z][A-Za-z0-9]*_[A-Za-z0-9_]+)(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "code",
    "does",
    "find",
    "for",
    "how",
    "in",
    "is",
    "of",
    "the",
    "this",
    "to",
    "what",
    "where",
    "which",
    "分析",
    "代码",
    "哪里",
    "如何",
    "是什么",
    "这个",
}
LANGUAGE_CUES = {
    "python": Language.PYTHON,
    "py": Language.PYTHON,
    "rust": Language.RUST,
    "javascript": Language.JAVASCRIPT,
    "js": Language.JAVASCRIPT,
    "typescript": Language.TYPESCRIPT,
    "ts": Language.TYPESCRIPT,
}


class RuleQueryAnalyzer:
    def analyze(self, query: str, filters: RetrievalFilters | None = None) -> QueryAnalysis:
        normalized = " ".join(query.strip().split())
        effective_filters = filters or RetrievalFilters()
        paths = tuple(dict.fromkeys(PATH.findall(normalized)))
        quoted = QUOTED.findall(normalized)
        identifiers = IDENTIFIER.findall(normalized)
        symbols = tuple(
            dict.fromkeys(
                candidate
                for candidate in quoted
                if IDENTIFIER.fullmatch(candidate) and "/" not in candidate
            )
        )
        symbols = tuple(
            dict.fromkeys(
                (
                    *symbols,
                    *(
                        candidate
                        for candidate in identifiers
                        if SYMBOL_SHAPE.fullmatch(candidate)
                        and candidate.lower() not in STOPWORDS
                        and candidate.lower() not in LANGUAGE_CUES
                    ),
                )
            )
        )
        keywords = tuple(
            dict.fromkeys(
                token
                for token in tokenize_code(normalized)
                if token not in STOPWORDS and token not in LANGUAGE_CUES and len(token) > 1
            )
        )
        inferred_languages = tuple(
            dict.fromkeys(
                LANGUAGE_CUES[token]
                for token in tokenize_code(normalized)
                if token in LANGUAGE_CUES
            )
        )
        if not effective_filters.languages and inferred_languages:
            effective_filters = RetrievalFilters(
                languages=inferred_languages,
                path_prefix=effective_filters.path_prefix,
            )
        return QueryAnalysis(
            original_query=query,
            semantic_query=normalized,
            intent=self._intent(normalized, symbols),
            keywords=keywords,
            symbols=symbols,
            path_terms=paths,
            filters=effective_filters,
        )

    @staticmethod
    def _intent(query: str, symbols: tuple[str, ...]) -> QueryIntent:
        lowered = query.lower()
        if any(cue in lowered for cue in ("调用", "call", "依赖", "trace")):
            return QueryIntent.TRACE_RELATIONS
        if symbols or any(cue in lowered for cue in ("哪里", "where", "locate", "find")):
            return QueryIntent.LOCATE_SYMBOL
        if any(cue in lowered for cue in ("实现", "implement")):
            return QueryIntent.LOCATE_IMPLEMENTATION
        if any(cue in lowered for cue in ("解释", "explain", "负责", "作用")):
            return QueryIntent.EXPLAIN_CODE
        return QueryIntent.GENERAL
