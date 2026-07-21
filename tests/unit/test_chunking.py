from __future__ import annotations

import hashlib

from codemind.domain.models import Language, SourceDocument
from codemind.indexing.chunker import AstCodeChunker
from codemind.parsing.tree_sitter_parser import TreeSitterCodeParser


def test_chunks_preserve_symbol_and_stable_identity() -> None:
    content = b"class Service:\n    def login(self):\n        return create_token()\n"
    document = SourceDocument(
        path="src/auth.py",
        language=Language.PYTHON,
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    parsed = TreeSitterCodeParser().parse(document)
    chunker = AstCodeChunker(max_chars=256, overlap_chars=32)

    first = chunker.chunk("repo-1", parsed)
    second = chunker.chunk("repo-1", parsed)

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert any(chunk.symbol_name == "Service.login" for chunk in first)
    assert all(chunk.start_line <= chunk.end_line for chunk in first)


def test_long_symbol_is_split_without_losing_line_ranges() -> None:
    body = "\n".join(f"    value_{index} = {index}" for index in range(100))
    content = f"def large():\n{body}\n".encode()
    document = SourceDocument(
        path="large.py",
        language=Language.PYTHON,
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    parsed = TreeSitterCodeParser().parse(document)

    chunks = AstCodeChunker(max_chars=256, overlap_chars=32).chunk("repo-1", parsed)

    assert len(chunks) > 2
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line >= 90
