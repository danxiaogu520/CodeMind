"""AST-aware chunking that preserves symbols and line citations."""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from codemind.domain.models import CodeChunk, ParsedFile, ParsedSymbol


class AstCodeChunker:
    def __init__(self, *, max_chars: int = 3_200, overlap_chars: int = 320) -> None:
        if max_chars < 256 or overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("Invalid chunk size configuration.")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def chunk(self, repository_id: str, parsed: ParsedFile) -> list[CodeChunk]:
        source = parsed.document.content.decode("utf-8", errors="replace")
        chunks: list[CodeChunk] = []
        top_level = [symbol for symbol in parsed.symbols if symbol.parent_local_id is None]
        first_symbol_byte = min(
            (symbol.start_byte for symbol in top_level), default=len(source.encode())
        )
        header = parsed.document.content[:first_symbol_byte].decode("utf-8", errors="replace")
        if header.strip():
            chunks.extend(
                self._split(
                    repository_id,
                    parsed,
                    header,
                    start_line=1,
                    symbol=None,
                )
            )

        for symbol in parsed.symbols:
            symbol_text = parsed.document.content[symbol.start_byte : symbol.end_byte].decode(
                "utf-8", errors="replace"
            )
            chunks.extend(
                self._split(
                    repository_id,
                    parsed,
                    symbol_text,
                    start_line=symbol.start_line,
                    symbol=symbol,
                )
            )

        if not chunks and source.strip():
            chunks.extend(self._split(repository_id, parsed, source, start_line=1, symbol=None))
        return chunks

    def _split(
        self,
        repository_id: str,
        parsed: ParsedFile,
        text: str,
        *,
        start_line: int,
        symbol: ParsedSymbol | None,
    ) -> list[CodeChunk]:
        result: list[CodeChunk] = []
        cursor = 0
        part = 0
        while cursor < len(text):
            target_end = min(cursor + self._max_chars, len(text))
            end = target_end
            if target_end < len(text):
                newline = text.rfind("\n", cursor + self._max_chars // 2, target_end)
                if newline > cursor:
                    end = newline + 1
            content = text[cursor:end]
            if content.strip():
                line_offset = text[:cursor].count("\n")
                chunk_start_line = start_line + line_offset
                chunk_end_line = chunk_start_line + content.count("\n")
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                identity = ":".join(
                    (
                        repository_id,
                        parsed.document.path,
                        symbol.qualified_name if symbol else "<file>",
                        str(part),
                        content_hash,
                    )
                )
                result.append(
                    CodeChunk(
                        id=str(uuid5(NAMESPACE_URL, identity)),
                        path=parsed.document.path,
                        language=parsed.document.language,
                        content=content,
                        content_hash=content_hash,
                        token_count=max(1, (len(content) + 3) // 4),
                        start_line=chunk_start_line,
                        end_line=max(chunk_start_line, chunk_end_line),
                        part=part,
                        symbol_local_id=symbol.local_id if symbol else None,
                        symbol_name=symbol.qualified_name if symbol else None,
                    )
                )
                part += 1
            if end >= len(text):
                break
            cursor = max(end - self._overlap_chars, cursor + 1)
        return result
