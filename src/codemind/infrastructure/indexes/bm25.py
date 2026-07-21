"""Small persistent BM25 index optimized for code tokens."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from codemind.domain.models import CodeChunk, Language, RetrievalFilters, SearchHit
from codemind.retrieval.tokenizer import tokenize_code


class SqliteBm25Index:
    def __init__(self, root: Path, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._root = root
        self._k1 = k1
        self._b = b

    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[CodeChunk]
    ) -> None:
        await asyncio.to_thread(self._replace_sync, repository_id, version_id, chunks)

    async def search(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        return await asyncio.to_thread(
            self._search_sync, repository_id, version_id, query, limit, filters
        )

    async def delete_version(self, version_id: str) -> None:
        await asyncio.to_thread(self._path(version_id).unlink, True)

    def _path(self, version_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", version_id):
            raise ValueError("Invalid index version ID.")
        return self._root / f"{version_id}.sqlite3"

    def _replace_sync(
        self, repository_id: str, version_id: str, chunks: Sequence[CodeChunk]
    ) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(version_id)
        temporary = path.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=OFF;
                CREATE TABLE documents (
                    chunk_id TEXT PRIMARY KEY,
                    repository_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    symbol_name TEXT,
                    content TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    length INTEGER NOT NULL,
                    term_frequencies TEXT NOT NULL
                );
                CREATE TABLE document_frequencies (
                    term TEXT PRIMARY KEY,
                    frequency INTEGER NOT NULL
                );
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            document_frequencies: Counter[str] = Counter()
            total_length = 0
            rows: list[tuple[object, ...]] = []
            for chunk in chunks:
                weighted_text = " ".join(
                    part for part in (chunk.path, chunk.symbol_name or "", chunk.content) if part
                )
                terms = tokenize_code(weighted_text)
                frequencies = Counter(terms)
                document_frequencies.update(frequencies.keys())
                total_length += len(terms)
                rows.append(
                    (
                        chunk.id,
                        repository_id,
                        chunk.path,
                        chunk.language.value,
                        chunk.symbol_name,
                        chunk.content,
                        chunk.start_line,
                        chunk.end_line,
                        len(terms),
                        json.dumps(frequencies, separators=(",", ":")),
                    )
                )
            connection.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
            connection.executemany(
                "INSERT INTO document_frequencies VALUES (?, ?)",
                document_frequencies.items(),
            )
            count = len(rows)
            average_length = total_length / count if count else 0.0
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                (("document_count", str(count)), ("average_length", str(average_length))),
            )
            connection.commit()
        finally:
            connection.close()
        temporary.replace(path)

    def _search_sync(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
    ) -> list[SearchHit]:
        path = self._path(version_id)
        if not path.is_file():
            return []
        query_terms = list(dict.fromkeys(tokenize_code(query)))
        if not query_terms:
            return []
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            metadata = {
                str(row["key"]): float(row["value"])
                for row in connection.execute("SELECT key, value FROM metadata")
            }
            document_count = int(metadata.get("document_count", 0))
            average_length = metadata.get("average_length", 0.0) or 1.0
            query_term_set = set(query_terms)
            frequencies = {
                str(row["term"]): int(row["frequency"])
                for row in connection.execute("SELECT term, frequency FROM document_frequencies")
                if str(row["term"]) in query_term_set
            }
            scored: list[tuple[float, sqlite3.Row]] = []
            for row in connection.execute(
                "SELECT * FROM documents WHERE repository_id = ?", (repository_id,)
            ):
                language = self._row_language(row)
                if filters is not None:
                    if filters.languages and language not in {
                        language.value for language in filters.languages
                    }:
                        continue
                    if filters.path_prefix and not str(row["path"]).startswith(filters.path_prefix):
                        continue
                term_frequencies = cast(dict[str, int], json.loads(row["term_frequencies"]))
                length = int(row["length"])
                score = 0.0
                for term in query_terms:
                    term_frequency = term_frequencies.get(term, 0)
                    if term_frequency == 0:
                        continue
                    document_frequency = frequencies.get(term, 0)
                    inverse_document_frequency = math.log(
                        1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                    )
                    denominator = term_frequency + self._k1 * (
                        1 - self._b + self._b * length / average_length
                    )
                    score += inverse_document_frequency * (
                        term_frequency * (self._k1 + 1) / denominator
                    )
                if score > 0:
                    scored.append((score, row))
            scored.sort(key=lambda item: (-item[0], str(item[1]["chunk_id"])))
            return [self._hit(score, row) for score, row in scored[:limit]]
        finally:
            connection.close()

    @classmethod
    def _hit(cls, score: float, row: sqlite3.Row) -> SearchHit:
        data: dict[str, Any] = dict(row)
        return SearchHit(
            chunk_id=str(data["chunk_id"]),
            score=score,
            path=str(data["path"]),
            content=str(data["content"]),
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
            symbol_name=str(data["symbol_name"]) if data["symbol_name"] else None,
            reasons=("bm25",),
            language=Language(cls._row_language(row)),
        )

    @staticmethod
    def _row_language(row: sqlite3.Row) -> str:
        columns = tuple(row.keys())
        if "language" in columns:
            return str(row["language"])
        suffix = Path(str(row["path"])).suffix.lower()
        return {
            ".py": Language.PYTHON.value,
            ".rs": Language.RUST.value,
            ".js": Language.JAVASCRIPT.value,
            ".jsx": Language.JAVASCRIPT.value,
            ".ts": Language.TYPESCRIPT.value,
            ".tsx": Language.TYPESCRIPT.value,
        }.get(suffix, Language.PYTHON.value)
