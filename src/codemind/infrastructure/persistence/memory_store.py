"""SQL-backed hierarchical project, session, and episodic memory."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codemind.application.ports import MemoryPort
from codemind.domain.models import (
    AgentAnswer,
    AgentRunRecord,
    Evidence,
    MemoryLayer,
    MemoryRecord,
)
from codemind.infrastructure.persistence.models import (
    MemoryModel,
    SourceFileModel,
    SymbolModel,
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[\u4e00-\u9fff]{2,}")


class SqlMemoryStore(MemoryPort):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def recall(
        self,
        repository_id: str,
        index_version_id: str,
        query: str,
        session_id: str | None,
        *,
        limit: int = 8,
    ) -> list[MemoryRecord]:
        async with self._sessions.begin() as session:
            statement = select(MemoryModel).where(
                MemoryModel.repository_id == repository_id,
                MemoryModel.index_version_id == index_version_id,
                or_(
                    MemoryModel.layer != MemoryLayer.SESSION.value,
                    MemoryModel.session_id == session_id,
                ),
            )
            models = list(await session.scalars(statement))
            query_tokens = self._tokens(query)
            scored = sorted(
                models,
                key=lambda item: (
                    -self._score(item, query_tokens, session_id),
                    item.layer,
                    item.scope_key,
                ),
            )[:limit]
            now = datetime.now(UTC)
            for item in scored:
                item.last_accessed_at = now
            return [self._record(item) for item in scored]

    async def refresh_project(self, repository_id: str, index_version_id: str) -> int:
        async with self._sessions.begin() as session:
            await session.execute(
                delete(MemoryModel).where(
                    MemoryModel.repository_id == repository_id,
                    MemoryModel.index_version_id == index_version_id,
                    MemoryModel.layer.in_([MemoryLayer.PROJECT.value, MemoryLayer.MODULE.value]),
                )
            )
            rows = (
                await session.execute(
                    select(SourceFileModel, SymbolModel)
                    .outerjoin(SymbolModel, SymbolModel.file_id == SourceFileModel.id)
                    .where(SourceFileModel.version_id == index_version_id)
                    .order_by(SourceFileModel.path, SymbolModel.start_line)
                )
            ).all()
            files: dict[str, SourceFileModel] = {}
            symbols: dict[str, list[str]] = defaultdict(list)
            for source_file, symbol in rows:
                files[source_file.path] = source_file
                if symbol is not None:
                    symbols[source_file.path].append(symbol.qualified_name)
            if not files:
                return 0
            languages: dict[str, int] = defaultdict(int)
            modules: dict[str, list[str]] = defaultdict(list)
            for path, source_file in files.items():
                languages[source_file.language] += 1
                modules[self._module(path)].append(path)
            language_summary = ", ".join(
                f"{name}={count}" for name, count in sorted(languages.items())
            )
            project_content = (
                f"Project contains {len(files)} indexed files. "
                f"Languages: {language_summary}. "
                f"Modules: {', '.join(sorted(modules))}."
            )
            session.add(
                self._model(
                    repository_id,
                    index_version_id,
                    MemoryLayer.PROJECT,
                    "project",
                    project_content,
                    list(files),
                    [files[path].content_hash for path in files],
                )
            )
            for module, paths in sorted(modules.items()):
                public_symbols = [name for path in paths for name in symbols[path]][:30]
                content = (
                    f"Module {module} contains {len(paths)} files: {', '.join(paths[:20])}. "
                    f"Symbols: {', '.join(public_symbols) or 'none detected'}."
                )
                session.add(
                    self._model(
                        repository_id,
                        index_version_id,
                        MemoryLayer.MODULE,
                        module,
                        content,
                        paths,
                        [files[path].content_hash for path in paths],
                    )
                )
            return len(modules) + 1

    async def rebase_version(
        self,
        repository_id: str,
        previous_version_id: str | None,
        index_version_id: str,
        changed_paths: Sequence[str],
    ) -> tuple[int, int]:
        if previous_version_id is None:
            return (0, 0)
        changed = set(changed_paths)
        invalidated = 0
        reused = 0
        async with self._sessions.begin() as session:
            memories = list(
                await session.scalars(
                    select(MemoryModel).where(
                        MemoryModel.repository_id == repository_id,
                        MemoryModel.index_version_id == previous_version_id,
                        MemoryModel.stale.is_(False),
                    )
                )
            )
            for memory in memories:
                sources = {str(path) for path in memory.source_paths_json}
                if memory.layer in {MemoryLayer.PROJECT.value, MemoryLayer.MODULE.value}:
                    memory.stale = True
                    invalidated += 1
                    continue
                if not sources or sources & changed:
                    memory.stale = True
                    invalidated += 1
                    continue
                session.add(
                    MemoryModel(
                        id=str(uuid4()),
                        repository_id=repository_id,
                        index_version_id=index_version_id,
                        layer=memory.layer,
                        scope_key=memory.scope_key,
                        session_id=memory.session_id,
                        session_key=memory.session_key,
                        run_id=memory.run_id,
                        content=memory.content,
                        evidence_ids_json=list(memory.evidence_ids_json),
                        source_paths_json=list(memory.source_paths_json),
                        source_hashes_json=list(memory.source_hashes_json),
                        stale=False,
                        confidence=memory.confidence,
                    )
                )
                reused += 1
        return (reused, invalidated)

    async def record_run(
        self,
        run: AgentRunRecord,
        answer: AgentAnswer,
        evidence: Sequence[Evidence],
    ) -> None:
        if not evidence or not answer.text:
            return
        paths = list(dict.fromkeys(item.path for item in evidence))
        evidence_ids = list(dict.fromkeys(item.id for item in evidence))
        async with self._sessions.begin() as session:
            hash_rows = (
                await session.execute(
                    select(SourceFileModel.path, SourceFileModel.content_hash).where(
                        SourceFileModel.version_id == run.index_version_id,
                        SourceFileModel.path.in_(paths),
                    )
                )
            ).all()
            hashes = {path: content_hash for path, content_hash in hash_rows}
            source_hashes = [str(hashes[path]) for path in paths if path in hashes]
            await self._upsert(
                session,
                repository_id=run.repository_id,
                index_version_id=run.index_version_id,
                layer=MemoryLayer.EPISODIC,
                scope_key=run.id,
                session_id=run.session_id,
                run_id=run.id,
                content=f"Question: {run.question}\nAnswer: {answer.text}",
                evidence_ids=evidence_ids,
                source_paths=paths,
                source_hashes=source_hashes,
            )
            if run.session_id:
                existing = await session.scalar(
                    select(MemoryModel).where(
                        MemoryModel.repository_id == run.repository_id,
                        MemoryModel.index_version_id == run.index_version_id,
                        MemoryModel.layer == MemoryLayer.SESSION.value,
                        MemoryModel.scope_key == "conversation",
                        MemoryModel.session_key == run.session_id,
                    )
                )
                content = f"Q: {run.question}\nA: {answer.text}"
                if existing is not None:
                    content = (
                        existing.content
                        if existing.run_id == run.id
                        else f"{existing.content}\n{content}"[-6000:]
                    )
                    evidence_ids = list(dict.fromkeys([*existing.evidence_ids_json, *evidence_ids]))
                    paths = list(dict.fromkeys([*existing.source_paths_json, *paths]))
                    source_hashes = list(
                        dict.fromkeys([*existing.source_hashes_json, *source_hashes])
                    )
                await self._upsert(
                    session,
                    repository_id=run.repository_id,
                    index_version_id=run.index_version_id,
                    layer=MemoryLayer.SESSION,
                    scope_key="conversation",
                    session_id=run.session_id,
                    run_id=run.id,
                    content=content,
                    evidence_ids=evidence_ids,
                    source_paths=paths,
                    source_hashes=source_hashes,
                )

    @staticmethod
    async def _upsert(
        session: AsyncSession,
        *,
        repository_id: str,
        index_version_id: str,
        layer: MemoryLayer,
        scope_key: str,
        session_id: str | None,
        run_id: str | None,
        content: str,
        evidence_ids: Sequence[str],
        source_paths: Sequence[str],
        source_hashes: Sequence[str],
    ) -> None:
        session_key = session_id or ""
        model = await session.scalar(
            select(MemoryModel).where(
                MemoryModel.repository_id == repository_id,
                MemoryModel.index_version_id == index_version_id,
                MemoryModel.layer == layer.value,
                MemoryModel.scope_key == scope_key,
                MemoryModel.session_key == session_key,
            )
        )
        if model is None:
            model = MemoryModel(
                id=str(uuid4()),
                repository_id=repository_id,
                index_version_id=index_version_id,
                layer=layer.value,
                scope_key=scope_key,
                session_id=session_id,
                session_key=session_key,
            )
            session.add(model)
        model.run_id = run_id
        model.content = content
        model.evidence_ids_json = list(evidence_ids)
        model.source_paths_json = list(source_paths)
        model.source_hashes_json = list(source_hashes)
        model.stale = False
        model.confidence = 1.0

    @staticmethod
    def _model(
        repository_id: str,
        index_version_id: str,
        layer: MemoryLayer,
        scope_key: str,
        content: str,
        source_paths: Sequence[str],
        source_hashes: Sequence[str],
    ) -> MemoryModel:
        return MemoryModel(
            id=str(uuid4()),
            repository_id=repository_id,
            index_version_id=index_version_id,
            layer=layer.value,
            scope_key=scope_key,
            session_id=None,
            session_key="",
            run_id=None,
            content=content,
            evidence_ids_json=[],
            source_paths_json=list(source_paths),
            source_hashes_json=list(source_hashes),
            stale=False,
            confidence=1.0,
        )

    @staticmethod
    def _record(model: MemoryModel) -> MemoryRecord:
        return MemoryRecord(
            id=model.id,
            repository_id=model.repository_id,
            index_version_id=model.index_version_id,
            layer=MemoryLayer(model.layer),
            scope_key=model.scope_key,
            content=model.content,
            session_id=model.session_id,
            run_id=model.run_id,
            evidence_ids=tuple(str(item) for item in model.evidence_ids_json),
            source_paths=tuple(str(item) for item in model.source_paths_json),
            source_hashes=tuple(str(item) for item in model.source_hashes_json),
            stale=model.stale,
            confidence=model.confidence,
        )

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {match.group(0).lower() for match in _TOKEN_PATTERN.finditer(value)}

    @classmethod
    def _score(cls, model: MemoryModel, query_tokens: set[str], session_id: str | None) -> float:
        memory_tokens = cls._tokens(f"{model.scope_key} {model.content}")
        overlap = len(query_tokens & memory_tokens) / max(1, len(query_tokens))
        layer_boost = {
            MemoryLayer.SESSION.value: 0.35 if model.session_id == session_id else 0.0,
            MemoryLayer.MODULE.value: 0.2,
            MemoryLayer.PROJECT.value: 0.1,
            MemoryLayer.EPISODIC.value: 0.25,
        }.get(model.layer, 0.0)
        return overlap + layer_boost + model.confidence * 0.01

    @staticmethod
    def _module(path: str) -> str:
        parts = PurePosixPath(path).parts
        if len(parts) <= 1:
            return "."
        return str(PurePosixPath(*parts[:-1]))
