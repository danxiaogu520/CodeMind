"""Version-bound, read-only Agent tool registry."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from codemind.application.retrieval import HybridRetrievalService
from codemind.domain.models import ToolContext, ToolResult
from codemind.infrastructure.persistence.models import (
    CodeChunkModel,
    RelationModel,
    SourceFileModel,
    SymbolModel,
)


class ReadOnlyCodeToolRegistry:
    names = frozenset(
        {
            "search_code",
            "get_file_outline",
            "read_symbol",
            "find_references",
            "list_tree",
            "get_project_summary",
        }
    )

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        retrieval: HybridRetrievalService,
    ) -> None:
        self._sessions = sessions
        self._retrieval = retrieval

    async def invoke(
        self, name: str, arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        if name not in self.names:
            raise ValueError(f"Tool is not allowed: {name}")
        if name == "search_code":
            query = self._required_string(arguments, "query")
            limit = min(max(self._integer(arguments, "limit", 10), 1), 20)
            result = await self._retrieval.search_version(
                context.repository_id, context.index_version_id, query, limit=limit
            )
            return ToolResult(
                name,
                {
                    "count": len(result.evidence),
                    "degraded_sources": list(result.degraded_sources),
                },
                result.evidence,
            )
        if name == "get_file_outline":
            return await self._file_outline(arguments, context)
        if name == "read_symbol":
            return await self._read_symbol(arguments, context)
        if name == "find_references":
            return await self._find_references(arguments, context)
        if name == "list_tree":
            return await self._list_tree(arguments, context)
        return await self._project_summary(context)

    async def _file_outline(
        self, arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        path = self._safe_path(self._required_string(arguments, "path"))
        statement = (
            select(SymbolModel)
            .join(SourceFileModel, SymbolModel.file_id == SourceFileModel.id)
            .where(
                SourceFileModel.version_id == context.index_version_id,
                SourceFileModel.path == path,
            )
            .order_by(SymbolModel.start_line)
        )
        async with self._sessions() as session:
            symbols = list(await session.scalars(statement))
        return ToolResult(
            "get_file_outline",
            {
                "path": path,
                "symbols": [
                    {
                        "qualified_name": symbol.qualified_name,
                        "kind": symbol.kind,
                        "signature": symbol.signature,
                        "start_line": symbol.start_line,
                        "end_line": symbol.end_line,
                    }
                    for symbol in symbols
                ],
            },
        )

    async def _read_symbol(
        self, arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        name = self._required_string(arguments, "qualified_name")
        statement = (
            select(SymbolModel, CodeChunkModel)
            .join(CodeChunkModel, CodeChunkModel.symbol_id == SymbolModel.id)
            .where(
                CodeChunkModel.version_id == context.index_version_id,
                or_(SymbolModel.qualified_name == name, SymbolModel.short_name == name),
            )
            .order_by(CodeChunkModel.part)
            .limit(20)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return ToolResult(
            "read_symbol",
            {
                "query": name,
                "matches": [
                    {
                        "qualified_name": symbol.qualified_name,
                        "path": chunk.path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content": chunk.content,
                    }
                    for symbol, chunk in rows
                ],
            },
        )

    async def _find_references(
        self, arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        name = self._required_string(arguments, "qualified_name")
        source = aliased(SymbolModel)
        target = aliased(SymbolModel)
        statement = (
            select(RelationModel, source, target)
            .outerjoin(source, RelationModel.source_symbol_id == source.id)
            .outerjoin(target, RelationModel.target_symbol_id == target.id)
            .where(
                RelationModel.version_id == context.index_version_id,
                or_(
                    source.qualified_name == name,
                    source.short_name == name,
                    target.qualified_name == name,
                    target.short_name == name,
                    RelationModel.target_text == name,
                ),
            )
            .limit(100)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return ToolResult(
            "find_references",
            {
                "symbol": name,
                "relations": [
                    {
                        "type": relation.type,
                        "source": source_symbol.qualified_name if source_symbol else None,
                        "target": (
                            target_symbol.qualified_name if target_symbol else relation.target_text
                        ),
                        "confidence": relation.confidence,
                    }
                    for relation, source_symbol, target_symbol in rows
                ],
            },
        )

    async def _list_tree(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        prefix_value = str(arguments.get("path", "")).strip()
        prefix = self._safe_path(prefix_value) if prefix_value else ""
        depth = min(max(self._integer(arguments, "depth", 2), 1), 5)
        statement = select(SourceFileModel.path).where(
            SourceFileModel.version_id == context.index_version_id
        )
        if prefix:
            statement = statement.where(SourceFileModel.path.startswith(prefix, autoescape=True))
        async with self._sessions() as session:
            paths = list(await session.scalars(statement.order_by(SourceFileModel.path)))
        base_depth = len(PurePosixPath(prefix).parts) if prefix else 0
        visible = [path for path in paths if len(PurePosixPath(path).parts) - base_depth <= depth][
            :500
        ]
        return ToolResult("list_tree", {"path": prefix, "depth": depth, "files": visible})

    async def _project_summary(self, context: ToolContext) -> ToolResult:
        language_statement = (
            select(SourceFileModel.language, func.count(SourceFileModel.id))
            .where(SourceFileModel.version_id == context.index_version_id)
            .group_by(SourceFileModel.language)
        )
        symbol_statement = (
            select(SymbolModel.kind, func.count(SymbolModel.id))
            .join(SourceFileModel, SymbolModel.file_id == SourceFileModel.id)
            .where(SourceFileModel.version_id == context.index_version_id)
            .group_by(SymbolModel.kind)
        )
        async with self._sessions() as session:
            languages = (await session.execute(language_statement)).all()
            symbols = (await session.execute(symbol_statement)).all()
        return ToolResult(
            "get_project_summary",
            {
                "languages": {language: count for language, count in languages},
                "symbol_kinds": {kind: count for kind, count in symbols},
            },
        )

    @staticmethod
    def _required_string(arguments: Mapping[str, object], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Tool argument '{key}' must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _integer(arguments: Mapping[str, object], key: str, default: int) -> int:
        value = arguments.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise ValueError(f"Tool argument '{key}' must be an integer.")

    @staticmethod
    def _safe_path(value: str) -> str:
        normalized = value.replace("\\", "/").strip("/")
        if not normalized or ".." in PurePosixPath(normalized).parts:
            raise ValueError("Tool path must be repository-relative and safe.")
        return normalized
