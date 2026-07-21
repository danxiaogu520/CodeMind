"""Repository indexing use case."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import structlog

from codemind.application.ports import (
    CodeChunker,
    CodeParser,
    EmbeddingProvider,
    FileDiscoverer,
    IndexStore,
    MemoryPort,
    MutableLexicalIndex,
    MutableVectorIndex,
    RepositorySource,
)
from codemind.domain.models import (
    CodeChunk,
    EmbeddedChunk,
    IndexDelta,
    IndexJobRecord,
    JobStatus,
    ParsedFile,
    ParseStatus,
)

logger = structlog.get_logger(__name__)


class IndexingService:
    """Build one immutable repository index version and atomically activate it."""

    parser_version = "tree-sitter-v2"

    def __init__(
        self,
        *,
        store: IndexStore,
        source: RepositorySource,
        discoverer: FileDiscoverer,
        parser: CodeParser,
        chunker: CodeChunker,
        embedding: EmbeddingProvider,
        vector_index: MutableVectorIndex,
        lexical_index: MutableLexicalIndex,
        memory: MemoryPort | None = None,
        embedding_batch_size: int = 64,
        retained_versions: int = 3,
    ) -> None:
        self._store = store
        self._source = source
        self._discoverer = discoverer
        self._parser = parser
        self._chunker = chunker
        self._embedding = embedding
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._memory = memory
        self._embedding_batch_size = embedding_batch_size
        self._retained_versions = retained_versions

    async def claim_job(self) -> IndexJobRecord | None:
        return await self._store.claim_job()

    async def recover_stale_jobs(
        self, *, stale_after_seconds: float, max_attempts: int
    ) -> tuple[int, int]:
        stale_before = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        return await self._store.recover_stale_jobs(stale_before, max_attempts)

    async def process(self, job: IndexJobRecord) -> None:
        repository = await self._store.get_repository(job.repository_id)
        if repository is None:
            await self._store.update_job(
                job.id,
                status=JobStatus.FAILED,
                stage="failed",
                error_summary="Repository does not exist.",
            )
            return

        prepared = None
        version_id = None
        try:
            await self._store.update_job(job.id, status=JobStatus.RUNNING, stage="preparing")
            prepared = await self._source.prepare(
                repository.id,
                repository.source_type,
                repository.source_uri,
                job.target_ref or repository.default_ref,
            )
            existing_version = await self._store.find_ready_version(
                repository.id,
                prepared.commit_sha,
                self.parser_version,
                self._embedding.model_id,
            )
            if existing_version is not None:
                await self._store.activate_version(repository.id, existing_version)
                await self._store.update_job(job.id, status=JobStatus.COMPLETED, stage="completed")
                await logger.ainfo(
                    "index_job_reused_version",
                    job_id=job.id,
                    repository_id=repository.id,
                    version_id=existing_version,
                )
                return
            version_id = await self._store.create_version(
                repository.id,
                prepared.commit_sha,
                self.parser_version,
                self._embedding.model_id,
            )

            await self._store.update_job(job.id, stage="discovering")
            documents = await self._discoverer.discover(prepared.root)
            await self._store.update_job(job.id, stage="parsing", total=len(documents))
            active_version_id = repository.active_index_version_id
            previous_identity = (
                await self._store.get_version_build_identity(active_version_id)
                if active_version_id is not None
                else None
            )
            previous_version_id = (
                active_version_id
                if previous_identity == (self.parser_version, self._embedding.model_id)
                else None
            )
            if active_version_id is not None and previous_version_id is None:
                await logger.ainfo(
                    "index_full_rebuild_for_build_identity_change",
                    repository_id=repository.id,
                    previous_identity=previous_identity,
                    current_identity=(self.parser_version, self._embedding.model_id),
                )
            previous_manifest = (
                await self._store.get_file_manifest(previous_version_id)
                if previous_version_id is not None
                else {}
            )
            current_manifest = {document.path: document.content_hash for document in documents}
            added_paths = set(current_manifest) - set(previous_manifest)
            deleted_paths = set(previous_manifest) - set(current_manifest)
            changed_paths = {
                path
                for path in set(current_manifest) & set(previous_manifest)
                if current_manifest[path] != previous_manifest[path]
            }
            unchanged_paths = {
                path
                for path in set(current_manifest) & set(previous_manifest)
                if current_manifest[path] == previous_manifest[path]
            }
            affected_paths = added_paths | changed_paths
            parsed_files: list[ParsedFile] = []
            chunks: list[CodeChunk] = []
            warnings = 0
            for index, document in enumerate(documents, start=1):
                if previous_version_id is None or document.path in affected_paths:
                    try:
                        parsed = self._parser.parse(document)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {str(exc)[:400]}"
                        parsed = ParsedFile(
                            document=document,
                            symbols=(),
                            relations=(),
                            status=ParseStatus.FAILED,
                            errors=(error,),
                        )
                        await logger.awarning(
                            "source_file_parse_failed",
                            job_id=job.id,
                            path=document.path,
                            error=error,
                        )
                    parsed_files.append(parsed)
                    chunks.extend(self._chunker.chunk(repository.id, parsed))
                    warnings += len(parsed.errors)
                await self._store.update_job(
                    job.id, processed=index, total=len(documents), warnings=warnings
                )

            await self._store.update_job(job.id, stage="persisting")
            if previous_version_id is None:
                await self._store.persist_parsed_files(version_id, parsed_files, chunks)
            else:
                await self._store.persist_incremental_files(
                    version_id,
                    previous_version_id,
                    sorted(unchanged_paths),
                    parsed_files,
                    chunks,
                )

            await self._store.update_job(job.id, stage="embedding")
            embedded = await self._embed_chunks(chunks)
            all_chunks = await self._store.list_chunks(version_id)
            if previous_version_id is None:
                await self._vector_index.replace_version(repository.id, version_id, embedded)
            else:
                await self._vector_index.build_incremental_version(
                    repository.id,
                    version_id,
                    previous_version_id,
                    sorted(unchanged_paths),
                    embedded,
                )
            await self._lexical_index.replace_version(repository.id, version_id, all_chunks)

            delta = IndexDelta(
                files_added=len(added_paths),
                files_changed=len(changed_paths),
                files_deleted=len(deleted_paths),
                files_reused=len(unchanged_paths),
                chunks_embedded=len(chunks),
                chunks_reused=max(0, len(all_chunks) - len(chunks)),
            )
            await self._store.update_job(job.id, delta=delta)
            if self._memory is not None:
                await self._memory.rebase_version(
                    repository.id,
                    active_version_id,
                    version_id,
                    sorted(changed_paths | deleted_paths),
                )
                await self._memory.refresh_project(repository.id, version_id)

            await self._store.update_job(job.id, stage="activating")
            await self._store.activate_version(repository.id, version_id)
            final_status = JobStatus.PARTIAL if warnings else JobStatus.COMPLETED
            await self._store.update_job(job.id, status=final_status, stage="completed")
            await logger.ainfo(
                "index_job_completed",
                job_id=job.id,
                repository_id=repository.id,
                files=len(parsed_files),
                chunks=len(chunks),
                warnings=warnings,
                delta=delta,
            )
            await self._prune_versions(repository.id)
        except Exception as exc:
            if version_id is not None:
                await self._store.fail_version(version_id)
            await self._store.update_job(
                job.id,
                status=JobStatus.FAILED,
                stage="failed",
                error_summary=f"{type(exc).__name__}: {str(exc)[:400]}",
            )
            await logger.aexception("index_job_failed", job_id=job.id)
        finally:
            if prepared is not None:
                await self._source.cleanup(prepared)

    async def _embed_chunks(self, chunks: Sequence[CodeChunk]) -> list[EmbeddedChunk]:
        result: list[EmbeddedChunk] = []
        for start in range(0, len(chunks), self._embedding_batch_size):
            batch = chunks[start : start + self._embedding_batch_size]
            texts = [
                "\n".join(
                    (
                        f"path: {chunk.path}",
                        f"language: {chunk.language.value}",
                        f"symbol: {chunk.symbol_name or '<file>'}",
                        chunk.content,
                    )
                )
                for chunk in batch
            ]
            vectors = await self._embedding.embed(texts)
            result.extend(
                EmbeddedChunk(chunk=chunk, vector=vector)
                for chunk, vector in zip(batch, vectors, strict=True)
            )
        return result

    async def _prune_versions(self, repository_id: str) -> None:
        version_ids = await self._store.list_prunable_versions(
            repository_id, self._retained_versions
        )
        for version_id in version_ids:
            try:
                await asyncio.gather(
                    self._vector_index.delete_version(version_id),
                    self._lexical_index.delete_version(version_id),
                )
                await self._store.delete_version(version_id)
            except Exception:
                await logger.aexception(
                    "index_version_prune_failed",
                    repository_id=repository_id,
                    version_id=version_id,
                )
