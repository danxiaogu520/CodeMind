"""PostgreSQL/SQLAlchemy implementation of the Phase 1 index store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from codemind.domain.models import (
    CodeChunk,
    IndexDelta,
    IndexJobRecord,
    JobStatus,
    Language,
    ParsedFile,
    RepositoryRecord,
    RetrievalFilters,
    SearchHit,
    SourceType,
    VersionStatus,
)
from codemind.infrastructure.persistence.models import (
    AgentRunModel,
    CodeChunkModel,
    IndexJobModel,
    IndexVersionModel,
    RelationModel,
    RepositoryModel,
    SourceFileModel,
    SymbolModel,
)


class SqlIndexStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_repository(
        self,
        name: str,
        source_type: SourceType,
        source_uri: str,
        default_ref: str | None,
    ) -> tuple[RepositoryRecord, IndexJobRecord]:
        repository_id = str(uuid4())
        job_id = str(uuid4())
        async with self._sessions.begin() as session:
            repository = RepositoryModel(
                id=repository_id,
                name=name,
                source_type=source_type.value,
                source_uri=source_uri,
                default_ref=default_ref,
                active_index_version_id=None,
            )
            job = IndexJobModel(
                id=job_id,
                repository_id=repository_id,
                target_ref=default_ref,
                status=JobStatus.QUEUED.value,
                stage="queued",
                processed=0,
                total=0,
                warnings=0,
                attempts=0,
            )
            session.add(repository)
            await session.flush()
            session.add(job)
        return self._repository_record(repository), self._job_record(job)

    async def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        async with self._sessions() as session:
            model = await session.get(RepositoryModel, repository_id)
            return self._repository_record(model) if model else None

    async def get_job(self, job_id: str) -> IndexJobRecord | None:
        async with self._sessions() as session:
            model = await session.get(IndexJobModel, job_id)
            return self._job_record(model) if model else None

    async def get_version_commit(self, version_id: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(IndexVersionModel.commit_sha).where(IndexVersionModel.id == version_id)
            )

    async def get_version_build_identity(self, version_id: str) -> tuple[str, str] | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        IndexVersionModel.parser_version,
                        IndexVersionModel.embedding_model,
                    ).where(IndexVersionModel.id == version_id)
                )
            ).one_or_none()
            return (str(row[0]), str(row[1])) if row is not None else None

    async def search_symbols(
        self,
        repository_id: str,
        version_id: str,
        terms: Sequence[str],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        del repository_id  # version ownership is enforced by the active version lookup
        normalized_terms = tuple(
            dict.fromkeys(term.strip().lower() for term in terms if term.strip())
        )[:20]
        statement = (
            select(CodeChunkModel, SymbolModel)
            .outerjoin(SymbolModel, CodeChunkModel.symbol_id == SymbolModel.id)
            .where(CodeChunkModel.version_id == version_id)
        )
        if filters is not None:
            if filters.languages:
                statement = statement.where(
                    CodeChunkModel.language.in_([language.value for language in filters.languages])
                )
            if filters.path_prefix:
                statement = statement.where(
                    CodeChunkModel.path.startswith(filters.path_prefix, autoescape=True)
                )
        if normalized_terms:
            predicates: list[ColumnElement[bool]] = []
            for term in normalized_terms:
                predicates.extend(
                    (
                        func.lower(CodeChunkModel.path).contains(term, autoescape=True),
                        func.lower(SymbolModel.qualified_name).contains(term, autoescape=True),
                        func.lower(SymbolModel.short_name).contains(term, autoescape=True),
                    )
                )
            statement = statement.where(or_(*predicates))
        elif filters is None or not filters.path_prefix:
            return []
        statement = statement.limit(max(limit * 8, 100))
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        scored: list[SearchHit] = []
        for chunk, symbol in rows:
            path = chunk.path.lower()
            qualified_name = symbol.qualified_name.lower() if symbol else ""
            short_name = symbol.short_name.lower() if symbol else ""
            score = 0.0
            reasons: list[str] = []
            for term in normalized_terms:
                if term in {qualified_name, short_name}:
                    score += 10.0
                    if "symbol_exact" not in reasons:
                        reasons.append("symbol_exact")
                elif qualified_name.startswith(term) or short_name.startswith(term):
                    score += 6.0
                    if "symbol_prefix" not in reasons:
                        reasons.append("symbol_prefix")
                elif term in qualified_name:
                    score += 3.0
                    if "symbol_match" not in reasons:
                        reasons.append("symbol_match")
                if term in path:
                    score += 2.0
                    if "path_match" not in reasons:
                        reasons.append("path_match")
            if filters is not None and filters.path_prefix:
                score += 1.0
                if "path_filter" not in reasons:
                    reasons.append("path_filter")
            if score <= 0:
                continue
            scored.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    score=score,
                    path=chunk.path,
                    content=chunk.content,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbol_name=symbol.qualified_name if symbol else None,
                    reasons=tuple(reasons),
                    language=Language(chunk.language),
                )
            )
        return sorted(scored, key=lambda hit: (-hit.score, hit.chunk_id))[:limit]

    async def enqueue_job(self, repository_id: str, target_ref: str | None) -> IndexJobRecord:
        model = IndexJobModel(
            id=str(uuid4()),
            repository_id=repository_id,
            target_ref=target_ref,
            status=JobStatus.QUEUED.value,
            stage="queued",
            processed=0,
            total=0,
            warnings=0,
            attempts=0,
        )
        async with self._sessions.begin() as session:
            session.add(model)
        return self._job_record(model)

    async def claim_job(self) -> IndexJobRecord | None:
        async with self._sessions.begin() as session:
            running_job = aliased(IndexJobModel)
            statement = (
                select(IndexJobModel)
                .where(
                    IndexJobModel.status == JobStatus.QUEUED.value,
                    ~exists().where(
                        running_job.repository_id == IndexJobModel.repository_id,
                        running_job.status == JobStatus.RUNNING.value,
                    ),
                )
                .order_by(IndexJobModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            model = (await session.scalars(statement)).first()
            if model is None:
                return None
            model.status = JobStatus.RUNNING.value
            model.stage = "claimed"
            model.attempts += 1
            model.started_at = model.started_at or datetime.now(UTC)
            model.heartbeat_at = datetime.now(UTC)
            await session.flush()
            return self._job_record(model)

    async def recover_stale_jobs(
        self, stale_before: datetime, max_attempts: int
    ) -> tuple[int, int]:
        """Requeue expired worker leases and terminally fail exhausted jobs."""

        requeued = 0
        failed = 0
        affected_repositories: set[str] = set()
        async with self._sessions.begin() as session:
            statement = (
                select(IndexJobModel)
                .where(
                    IndexJobModel.status == JobStatus.RUNNING.value,
                    IndexJobModel.heartbeat_at < stale_before,
                )
                .order_by(IndexJobModel.heartbeat_at)
                .with_for_update(skip_locked=True)
            )
            jobs = list(await session.scalars(statement))
            now = datetime.now(UTC)
            for job in jobs:
                affected_repositories.add(job.repository_id)
                if job.attempts >= max_attempts:
                    job.status = JobStatus.FAILED.value
                    job.stage = "failed"
                    job.finished_at = now
                    job.error_summary = f"Worker lease expired after {job.attempts} attempts."
                    failed += 1
                    continue
                job.status = JobStatus.QUEUED.value
                job.stage = "recovered"
                job.processed = 0
                job.total = 0
                job.warnings = 0
                job.error_summary = None
                job.finished_at = None
                job.heartbeat_at = None
                requeued += 1
            if affected_repositories:
                await session.execute(
                    update(IndexVersionModel)
                    .where(
                        IndexVersionModel.repository_id.in_(affected_repositories),
                        IndexVersionModel.status == VersionStatus.BUILDING.value,
                    )
                    .values(status=VersionStatus.FAILED.value)
                )
        return requeued, failed

    async def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: str | None = None,
        processed: int | None = None,
        total: int | None = None,
        warnings: int | None = None,
        error_summary: str | None = None,
        delta: IndexDelta | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            model = await session.get(IndexJobModel, job_id)
            if model is None:
                return
            if status is not None:
                model.status = status.value
                if status in {
                    JobStatus.COMPLETED,
                    JobStatus.PARTIAL,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }:
                    model.finished_at = datetime.now(UTC)
            if stage is not None:
                model.stage = stage
            if processed is not None:
                model.processed = processed
            if total is not None:
                model.total = total
            if warnings is not None:
                model.warnings = warnings
            if error_summary is not None:
                model.error_summary = error_summary
            if delta is not None:
                model.delta_json = {
                    "files_added": delta.files_added,
                    "files_changed": delta.files_changed,
                    "files_deleted": delta.files_deleted,
                    "files_reused": delta.files_reused,
                    "chunks_embedded": delta.chunks_embedded,
                    "chunks_reused": delta.chunks_reused,
                }
            model.heartbeat_at = datetime.now(UTC)

    async def create_version(
        self, repository_id: str, commit_sha: str, parser_version: str, embedding_model: str
    ) -> str:
        version_id = str(uuid4())
        async with self._sessions.begin() as session:
            existing = (
                await session.scalars(
                    select(IndexVersionModel)
                    .where(
                        IndexVersionModel.repository_id == repository_id,
                        IndexVersionModel.commit_sha == commit_sha,
                        IndexVersionModel.parser_version == parser_version,
                        IndexVersionModel.embedding_model == embedding_model,
                    )
                    .with_for_update()
                )
            ).first()
            if existing is not None:
                if existing.status == VersionStatus.READY.value:
                    raise RuntimeError("Ready index version must be reused before creation.")
                await session.execute(
                    delete(RelationModel).where(RelationModel.version_id == existing.id)
                )
                await session.execute(
                    delete(SourceFileModel).where(SourceFileModel.version_id == existing.id)
                )
                existing.status = VersionStatus.BUILDING.value
                existing.activated_at = None
                return existing.id
            session.add(
                IndexVersionModel(
                    id=version_id,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    parser_version=parser_version,
                    embedding_model=embedding_model,
                    status=VersionStatus.BUILDING.value,
                )
            )
        return version_id

    async def find_ready_version(
        self, repository_id: str, commit_sha: str, parser_version: str, embedding_model: str
    ) -> str | None:
        statement = select(IndexVersionModel.id).where(
            IndexVersionModel.repository_id == repository_id,
            IndexVersionModel.commit_sha == commit_sha,
            IndexVersionModel.parser_version == parser_version,
            IndexVersionModel.embedding_model == embedding_model,
            IndexVersionModel.status == VersionStatus.READY.value,
        )
        async with self._sessions() as session:
            return (await session.scalars(statement)).first()

    async def persist_parsed_files(
        self, version_id: str, files: Sequence[ParsedFile], chunks: Sequence[CodeChunk]
    ) -> None:
        file_ids: dict[str, str] = {}
        symbol_ids: dict[tuple[str, str], str] = {}
        qualified_symbols: dict[str, str] = {}
        short_symbols: dict[str, str] = {}
        async with self._sessions.begin() as session:
            for parsed in files:
                file_id = str(uuid4())
                file_ids[parsed.document.path] = file_id
                session.add(
                    SourceFileModel(
                        id=file_id,
                        version_id=version_id,
                        path=parsed.document.path,
                        language=parsed.document.language.value,
                        content_hash=parsed.document.content_hash,
                        size_bytes=len(parsed.document.content),
                        parse_status=parsed.status.value,
                        error_summary="; ".join(parsed.errors) or None,
                    )
                )
                await session.flush()
                for symbol in parsed.symbols:
                    symbol_id = str(uuid4())
                    symbol_ids[(parsed.document.path, symbol.local_id)] = symbol_id
                    qualified_symbols[symbol.qualified_name] = symbol_id
                    short_symbols.setdefault(symbol.name, symbol_id)
                    session.add(
                        SymbolModel(
                            id=symbol_id,
                            file_id=file_id,
                            qualified_name=symbol.qualified_name,
                            short_name=symbol.name,
                            kind=symbol.kind.value,
                            signature=symbol.signature,
                            start_line=symbol.start_line,
                            end_line=symbol.end_line,
                        )
                    )

            await session.flush()

            for parsed in files:
                for relation in parsed.relations:
                    source_id = (
                        symbol_ids.get((parsed.document.path, relation.source_local_id))
                        if relation.source_local_id
                        else None
                    )
                    target_id = qualified_symbols.get(relation.target_text) or short_symbols.get(
                        relation.target_text.rsplit(".", 1)[-1]
                    )
                    session.add(
                        RelationModel(
                            id=str(uuid4()),
                            version_id=version_id,
                            source_symbol_id=source_id,
                            target_symbol_id=target_id,
                            target_text=relation.target_text,
                            type=relation.type.value,
                            confidence=relation.confidence,
                        )
                    )

            for chunk in chunks:
                file_id = file_ids[chunk.path]
                symbol_id = (
                    symbol_ids.get((chunk.path, chunk.symbol_local_id))
                    if chunk.symbol_local_id
                    else None
                )
                record_id = str(uuid5(NAMESPACE_URL, f"{version_id}:{chunk.id}"))
                session.add(
                    CodeChunkModel(
                        record_id=record_id,
                        chunk_id=chunk.id,
                        version_id=version_id,
                        file_id=file_id,
                        symbol_id=symbol_id,
                        path=chunk.path,
                        language=chunk.language.value,
                        part=chunk.part,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        content_hash=chunk.content_hash,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                    )
                )

    async def get_file_manifest(self, version_id: str) -> dict[str, str]:
        async with self._sessions() as session:
            rows = await session.execute(
                select(SourceFileModel.path, SourceFileModel.content_hash).where(
                    SourceFileModel.version_id == version_id
                )
            )
            return {str(path): str(content_hash) for path, content_hash in rows}

    async def persist_incremental_files(
        self,
        version_id: str,
        base_version_id: str,
        unchanged_paths: Sequence[str],
        files: Sequence[ParsedFile],
        chunks: Sequence[CodeChunk],
    ) -> None:
        unchanged = set(unchanged_paths)
        file_ids: dict[str, str] = {}
        old_to_new_symbols: dict[str, str] = {}
        local_symbols: dict[tuple[str, str], str] = {}
        qualified_symbols: dict[str, str] = {}
        short_symbols: dict[str, str] = {}
        async with self._sessions.begin() as session:
            old_files = list(
                await session.scalars(
                    select(SourceFileModel).where(
                        SourceFileModel.version_id == base_version_id,
                        SourceFileModel.path.in_(unchanged),
                    )
                )
            )
            for old_file in old_files:
                file_id = str(uuid4())
                file_ids[old_file.path] = file_id
                session.add(
                    SourceFileModel(
                        id=file_id,
                        version_id=version_id,
                        path=old_file.path,
                        language=old_file.language,
                        content_hash=old_file.content_hash,
                        size_bytes=old_file.size_bytes,
                        parse_status=old_file.parse_status,
                        error_summary=old_file.error_summary,
                    )
                )
                old_symbols = list(
                    await session.scalars(
                        select(SymbolModel).where(SymbolModel.file_id == old_file.id)
                    )
                )
                for old_symbol in old_symbols:
                    symbol_id = str(uuid4())
                    old_to_new_symbols[old_symbol.id] = symbol_id
                    qualified_symbols[old_symbol.qualified_name] = symbol_id
                    short_symbols.setdefault(old_symbol.short_name, symbol_id)
                    session.add(
                        SymbolModel(
                            id=symbol_id,
                            file_id=file_id,
                            qualified_name=old_symbol.qualified_name,
                            short_name=old_symbol.short_name,
                            kind=old_symbol.kind,
                            signature=old_symbol.signature,
                            start_line=old_symbol.start_line,
                            end_line=old_symbol.end_line,
                        )
                    )

            for parsed in files:
                file_id = str(uuid4())
                file_ids[parsed.document.path] = file_id
                session.add(
                    SourceFileModel(
                        id=file_id,
                        version_id=version_id,
                        path=parsed.document.path,
                        language=parsed.document.language.value,
                        content_hash=parsed.document.content_hash,
                        size_bytes=len(parsed.document.content),
                        parse_status=parsed.status.value,
                        error_summary="; ".join(parsed.errors) or None,
                    )
                )
                for symbol in parsed.symbols:
                    symbol_id = str(uuid4())
                    local_symbols[(parsed.document.path, symbol.local_id)] = symbol_id
                    qualified_symbols[symbol.qualified_name] = symbol_id
                    short_symbols.setdefault(symbol.name, symbol_id)
                    session.add(
                        SymbolModel(
                            id=symbol_id,
                            file_id=file_id,
                            qualified_name=symbol.qualified_name,
                            short_name=symbol.name,
                            kind=symbol.kind.value,
                            signature=symbol.signature,
                            start_line=symbol.start_line,
                            end_line=symbol.end_line,
                        )
                    )
            await session.flush()

            old_relations = list(
                await session.scalars(
                    select(RelationModel).where(RelationModel.version_id == base_version_id)
                )
            )
            for relation in old_relations:
                if (
                    relation.source_symbol_id
                    and relation.source_symbol_id not in old_to_new_symbols
                ):
                    continue
                target_id = qualified_symbols.get(relation.target_text) or short_symbols.get(
                    relation.target_text.rsplit(".", 1)[-1]
                )
                session.add(
                    RelationModel(
                        id=str(uuid4()),
                        version_id=version_id,
                        source_symbol_id=(
                            old_to_new_symbols.get(relation.source_symbol_id)
                            if relation.source_symbol_id
                            else None
                        ),
                        target_symbol_id=target_id,
                        target_text=relation.target_text,
                        type=relation.type,
                        confidence=relation.confidence,
                    )
                )

            for parsed in files:
                for relation in parsed.relations:
                    source_id = (
                        local_symbols.get((parsed.document.path, relation.source_local_id))
                        if relation.source_local_id
                        else None
                    )
                    target_id = qualified_symbols.get(relation.target_text) or short_symbols.get(
                        relation.target_text.rsplit(".", 1)[-1]
                    )
                    session.add(
                        RelationModel(
                            id=str(uuid4()),
                            version_id=version_id,
                            source_symbol_id=source_id,
                            target_symbol_id=target_id,
                            target_text=relation.target_text,
                            type=relation.type.value,
                            confidence=relation.confidence,
                        )
                    )

            old_chunks = list(
                await session.scalars(
                    select(CodeChunkModel).where(
                        CodeChunkModel.version_id == base_version_id,
                        CodeChunkModel.path.in_(unchanged),
                    )
                )
            )
            for old_chunk in old_chunks:
                session.add(
                    CodeChunkModel(
                        record_id=str(uuid5(NAMESPACE_URL, f"{version_id}:{old_chunk.chunk_id}")),
                        chunk_id=old_chunk.chunk_id,
                        version_id=version_id,
                        file_id=file_ids[old_chunk.path],
                        symbol_id=(
                            old_to_new_symbols.get(old_chunk.symbol_id)
                            if old_chunk.symbol_id
                            else None
                        ),
                        path=old_chunk.path,
                        language=old_chunk.language,
                        part=old_chunk.part,
                        content=old_chunk.content,
                        token_count=old_chunk.token_count,
                        content_hash=old_chunk.content_hash,
                        start_line=old_chunk.start_line,
                        end_line=old_chunk.end_line,
                    )
                )
            for chunk in chunks:
                session.add(
                    CodeChunkModel(
                        record_id=str(uuid5(NAMESPACE_URL, f"{version_id}:{chunk.id}")),
                        chunk_id=chunk.id,
                        version_id=version_id,
                        file_id=file_ids[chunk.path],
                        symbol_id=(
                            local_symbols.get((chunk.path, chunk.symbol_local_id))
                            if chunk.symbol_local_id
                            else None
                        ),
                        path=chunk.path,
                        language=chunk.language.value,
                        part=chunk.part,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        content_hash=chunk.content_hash,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                    )
                )

    async def list_chunks(self, version_id: str) -> list[CodeChunk]:
        statement = (
            select(CodeChunkModel, SymbolModel.qualified_name)
            .outerjoin(SymbolModel, CodeChunkModel.symbol_id == SymbolModel.id)
            .where(CodeChunkModel.version_id == version_id)
            .order_by(CodeChunkModel.path, CodeChunkModel.start_line, CodeChunkModel.part)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            CodeChunk(
                id=chunk.chunk_id,
                path=chunk.path,
                language=Language(chunk.language),
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                part=chunk.part,
                symbol_name=qualified_name,
            )
            for chunk, qualified_name in rows
        ]

    async def list_prunable_versions(self, repository_id: str, keep: int) -> list[str]:
        async with self._sessions() as session:
            repository = await session.get(RepositoryModel, repository_id)
            if repository is None:
                return []
            referenced = set(
                await session.scalars(
                    select(AgentRunModel.index_version_id).where(
                        AgentRunModel.repository_id == repository_id
                    )
                )
            )
            versions = list(
                await session.scalars(
                    select(IndexVersionModel.id)
                    .where(IndexVersionModel.repository_id == repository_id)
                    .order_by(
                        IndexVersionModel.activated_at.desc().nullslast(),
                        IndexVersionModel.created_at.desc(),
                        IndexVersionModel.id.desc(),
                    )
                )
            )
        protected = referenced | {repository.active_index_version_id}
        retained = set(versions[: max(1, keep)]) | protected
        return [version_id for version_id in versions if version_id not in retained]

    async def delete_version(self, version_id: str) -> bool:
        try:
            async with self._sessions.begin() as session:
                await session.execute(
                    delete(IndexVersionModel).where(IndexVersionModel.id == version_id)
                )
                return True
        except IntegrityError:
            return False

    async def activate_version(self, repository_id: str, version_id: str) -> None:
        async with self._sessions.begin() as session:
            version = await session.get(IndexVersionModel, version_id)
            repository = await session.get(RepositoryModel, repository_id)
            if version is None or repository is None:
                raise LookupError("Repository index version cannot be activated.")
            version.status = VersionStatus.READY.value
            version.activated_at = datetime.now(UTC)
            repository.active_index_version_id = version_id

    async def fail_version(self, version_id: str) -> None:
        async with self._sessions.begin() as session:
            version = await session.get(IndexVersionModel, version_id)
            if version is not None:
                version.status = VersionStatus.FAILED.value

    @staticmethod
    def _repository_record(model: RepositoryModel) -> RepositoryRecord:
        return RepositoryRecord(
            id=model.id,
            name=model.name,
            source_type=SourceType(model.source_type),
            source_uri=model.source_uri,
            default_ref=model.default_ref,
            active_index_version_id=model.active_index_version_id,
        )

    @staticmethod
    def _job_record(model: IndexJobModel) -> IndexJobRecord:
        raw_delta = model.delta_json or {}
        return IndexJobRecord(
            id=model.id,
            repository_id=model.repository_id,
            status=JobStatus(model.status),
            stage=model.stage,
            target_ref=model.target_ref,
            processed=model.processed,
            total=model.total,
            warnings=model.warnings,
            error_summary=model.error_summary,
            delta=IndexDelta(
                files_added=int(str(raw_delta.get("files_added", 0))),
                files_changed=int(str(raw_delta.get("files_changed", 0))),
                files_deleted=int(str(raw_delta.get("files_deleted", 0))),
                files_reused=int(str(raw_delta.get("files_reused", 0))),
                chunks_embedded=int(str(raw_delta.get("chunks_embedded", 0))),
                chunks_reused=int(str(raw_delta.get("chunks_reused", 0))),
            ),
        )
