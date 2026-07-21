"""Application ports for repository indexing and retrieval infrastructure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from codemind.domain.models import (
    AgentAnswer,
    AgentRunRecord,
    AgentRunStatus,
    CodeChunk,
    EmbeddedChunk,
    Evidence,
    IndexDelta,
    IndexJobRecord,
    JobStatus,
    MemoryRecord,
    ParsedFile,
    PreparedRepository,
    QueryAnalysis,
    RankedHit,
    RepositoryRecord,
    RetrievalFilters,
    RunBudgets,
    RunEvent,
    SearchHit,
    SourceDocument,
    SourceType,
    ToolContext,
    ToolResult,
    WorkflowKind,
)


class RepositorySource(Protocol):
    async def prepare(
        self, repository_id: str, source_type: SourceType, source_uri: str, ref: str | None
    ) -> PreparedRepository: ...

    async def cleanup(self, prepared: PreparedRepository) -> None: ...


class FileDiscoverer(Protocol):
    async def discover(self, root: Path) -> list[SourceDocument]: ...


class CodeParser(Protocol):
    def parse(self, document: SourceDocument) -> ParsedFile: ...


class CodeChunker(Protocol):
    def chunk(self, repository_id: str, parsed: ParsedFile) -> list[CodeChunk]: ...


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


@runtime_checkable
class QueryEmbeddingProvider(Protocol):
    """Optional capability for models that distinguish query and document inputs."""

    async def embed_query(self, text: str) -> tuple[float, ...]: ...


class VectorIndex(Protocol):
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[EmbeddedChunk]
    ) -> None: ...

    async def search(
        self,
        repository_id: str,
        version_id: str,
        vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]: ...


class MutableVectorIndex(VectorIndex, Protocol):
    async def build_incremental_version(
        self,
        repository_id: str,
        version_id: str,
        base_version_id: str,
        unchanged_paths: Sequence[str],
        changed_chunks: Sequence[EmbeddedChunk],
    ) -> None: ...

    async def delete_version(self, version_id: str) -> None: ...


class LexicalIndex(Protocol):
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[CodeChunk]
    ) -> None: ...

    async def search(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]: ...


class MutableLexicalIndex(LexicalIndex, Protocol):
    async def delete_version(self, version_id: str) -> None: ...


class SymbolIndex(Protocol):
    async def search_symbols(
        self,
        repository_id: str,
        version_id: str,
        terms: Sequence[str],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]: ...


class RerankerProvider(Protocol):
    model_id: str

    async def rerank(self, query: str, hits: Sequence[RankedHit]) -> list[float]: ...


class QueryAnalyzer(Protocol):
    def analyze(self, query: str, filters: RetrievalFilters | None = None) -> QueryAnalysis: ...


class LLMProvider(Protocol):
    model_id: str

    async def generate_grounded_answer(
        self,
        workflow: WorkflowKind,
        question: str,
        evidence: Sequence[Evidence],
        observations: Sequence[dict[str, object]],
    ) -> AgentAnswer: ...


class AgentTool(Protocol):
    name: str

    async def invoke(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult: ...


class AgentRunStore(Protocol):
    async def create_run(
        self,
        repository_id: str,
        workflow: WorkflowKind,
        question: str,
        session_id: str | None,
        budgets: RunBudgets,
    ) -> AgentRunRecord: ...

    async def get_run(self, run_id: str) -> AgentRunRecord | None: ...
    async def list_resumable_runs(self) -> list[AgentRunRecord]: ...
    async def is_cancel_requested(self, run_id: str) -> bool: ...
    async def request_cancel(self, run_id: str) -> AgentRunRecord | None: ...
    async def mark_run(
        self,
        run_id: str,
        status: AgentRunStatus,
        *,
        answer: AgentAnswer | None = None,
        error_summary: str | None = None,
        steps_used: int | None = None,
        tool_calls_used: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None: ...

    async def record_step(
        self,
        run_id: str,
        node_name: str,
        sequence: int,
        status: str,
        *,
        input_summary: str | None = None,
        output_summary: str | None = None,
        error_summary: str | None = None,
    ) -> None: ...

    async def append_event(
        self,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        data: dict[str, object],
    ) -> RunEvent: ...

    async def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]: ...


class WorkflowRuntime(Protocol):
    async def execute(self, run: AgentRunRecord) -> None: ...


class MemoryPort(Protocol):
    async def recall(
        self,
        repository_id: str,
        index_version_id: str,
        query: str,
        session_id: str | None,
        *,
        limit: int = 8,
    ) -> list[MemoryRecord]: ...

    async def refresh_project(self, repository_id: str, index_version_id: str) -> int: ...

    async def rebase_version(
        self,
        repository_id: str,
        previous_version_id: str | None,
        index_version_id: str,
        changed_paths: Sequence[str],
    ) -> tuple[int, int]: ...

    async def record_run(
        self,
        run: AgentRunRecord,
        answer: AgentAnswer,
        evidence: Sequence[Evidence],
    ) -> None: ...


class IndexStore(Protocol):
    async def create_repository(
        self,
        name: str,
        source_type: SourceType,
        source_uri: str,
        default_ref: str | None,
    ) -> tuple[RepositoryRecord, IndexJobRecord]: ...

    async def get_repository(self, repository_id: str) -> RepositoryRecord | None: ...
    async def get_version_commit(self, version_id: str) -> str | None: ...
    async def get_version_build_identity(self, version_id: str) -> tuple[str, str] | None: ...
    async def get_job(self, job_id: str) -> IndexJobRecord | None: ...
    async def enqueue_job(self, repository_id: str, target_ref: str | None) -> IndexJobRecord: ...
    async def claim_job(self) -> IndexJobRecord | None: ...
    async def recover_stale_jobs(
        self, stale_before: datetime, max_attempts: int
    ) -> tuple[int, int]: ...

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
    ) -> None: ...

    async def create_version(
        self, repository_id: str, commit_sha: str, parser_version: str, embedding_model: str
    ) -> str: ...

    async def find_ready_version(
        self, repository_id: str, commit_sha: str, parser_version: str, embedding_model: str
    ) -> str | None: ...

    async def persist_parsed_files(
        self, version_id: str, files: Sequence[ParsedFile], chunks: Sequence[CodeChunk]
    ) -> None: ...

    async def get_file_manifest(self, version_id: str) -> dict[str, str]: ...

    async def persist_incremental_files(
        self,
        version_id: str,
        base_version_id: str,
        unchanged_paths: Sequence[str],
        files: Sequence[ParsedFile],
        chunks: Sequence[CodeChunk],
    ) -> None: ...

    async def list_chunks(self, version_id: str) -> list[CodeChunk]: ...

    async def list_prunable_versions(self, repository_id: str, keep: int) -> list[str]: ...

    async def delete_version(self, version_id: str) -> bool: ...

    async def activate_version(self, repository_id: str, version_id: str) -> None: ...
    async def fail_version(self, version_id: str) -> None: ...
