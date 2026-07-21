from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from codemind.domain.models import CodeChunk, Language, RetrievalFilters
from codemind.indexing.embedding import HashEmbeddingProvider
from codemind.infrastructure.indexes.bm25 import SqliteBm25Index, tokenize_code


def make_chunk(chunk_id: str, path: str, symbol: str, content: str) -> CodeChunk:
    return CodeChunk(
        id=chunk_id,
        path=path,
        language=Language.PYTHON,
        content=content,
        content_hash=chunk_id,
        token_count=10,
        start_line=1,
        end_line=3,
        part=0,
        symbol_name=symbol,
    )


def test_code_tokenizer_splits_camel_and_snake_case() -> None:
    tokens = tokenize_code("getUserById get_user_by_id")

    assert "getuserbyid" in tokens
    assert tokens.count("user") == 2
    assert tokens.count("id") == 2


async def test_hash_embedding_is_deterministic_and_normalized() -> None:
    provider = HashEmbeddingProvider(64)

    first, second = await provider.embed(["create token", "create token"])

    assert first == second
    assert math.isclose(sum(value * value for value in first), 1.0)


async def test_bm25_index_persists_and_ranks_exact_code_terms(tmp_path: Path) -> None:
    version_id = "11111111-1111-1111-1111-111111111111"
    chunks = [
        make_chunk("a", "src/auth.py", "AuthService.login", "return create_token(user)"),
        make_chunk("b", "src/cache.py", "Cache.get", "return cached_value"),
    ]
    index = SqliteBm25Index(tmp_path)
    await index.replace_version("repo-1", version_id, chunks)

    reloaded = SqliteBm25Index(tmp_path)
    hits = await reloaded.search("repo-1", version_id, "createToken", 10)

    assert hits[0].chunk_id == "a"
    assert hits[0].reasons == ("bm25",)


async def test_bm25_reads_phase1_index_without_language_column(tmp_path: Path) -> None:
    version_id = "22222222-2222-2222-2222-222222222222"
    path = tmp_path / f"{version_id}.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE documents (
            chunk_id TEXT PRIMARY KEY, repository_id TEXT, path TEXT, symbol_name TEXT,
            content TEXT, start_line INTEGER, end_line INTEGER, length INTEGER,
            term_frequencies TEXT
        );
        CREATE TABLE document_frequencies (term TEXT PRIMARY KEY, frequency INTEGER);
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO documents VALUES
            ('legacy', 'repo-1', 'src/legacy.py', 'legacy_token', 'legacy token', 1, 2, 2,
             '{"legacy":1,"token":1}');
        INSERT INTO document_frequencies VALUES ('legacy', 1), ('token', 1);
        INSERT INTO metadata VALUES ('document_count', '1'), ('average_length', '2');
        """
    )
    connection.commit()
    connection.close()

    hits = await SqliteBm25Index(tmp_path).search(
        "repo-1",
        version_id,
        "legacy_token",
        10,
        RetrievalFilters(languages=(Language.PYTHON,)),
    )

    assert hits[0].language is Language.PYTHON
