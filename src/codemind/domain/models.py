"""Framework-independent entities used by repository indexing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class SourceType(StrEnum):
    LOCAL = "local"
    GIT = "git"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VersionStatus(StrEnum):
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class ParseStatus(StrEnum):
    PARSED = "parsed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class Language(StrEnum):
    PYTHON = "python"
    RUST = "rust"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    TRAIT = "trait"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    IMPL = "impl"


class RelationType(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    REFERENCES = "REFERENCES"


class QueryIntent(StrEnum):
    LOCATE_SYMBOL = "locate_symbol"
    LOCATE_IMPLEMENTATION = "locate_implementation"
    EXPLAIN_CODE = "explain_code"
    TRACE_RELATIONS = "trace_relations"
    GENERAL = "general"


class WorkflowKind(StrEnum):
    AUTO = "auto"
    ANSWER_QUESTION = "answer_question"
    EXPLAIN_MODULE = "explain_module"
    TRACE_SYMBOL = "trace_symbol"


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MemoryLayer(StrEnum):
    SESSION = "session"
    PROJECT = "project"
    MODULE = "module"
    EPISODIC = "episodic"


@dataclass(frozen=True, slots=True)
class RepositoryRecord:
    id: str
    name: str
    source_type: SourceType
    source_uri: str
    default_ref: str | None
    active_index_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class IndexJobRecord:
    id: str
    repository_id: str
    status: JobStatus
    stage: str
    target_ref: str | None
    processed: int = 0
    total: int = 0
    warnings: int = 0
    error_summary: str | None = None
    delta: IndexDelta = field(default_factory=lambda: IndexDelta())


@dataclass(frozen=True, slots=True)
class IndexDelta:
    files_added: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    files_reused: int = 0
    chunks_embedded: int = 0
    chunks_reused: int = 0


@dataclass(frozen=True, slots=True)
class PreparedRepository:
    root: Path
    commit_sha: str
    cleanup_required: bool = False


@dataclass(frozen=True, slots=True)
class SourceDocument:
    path: str
    language: Language
    content: bytes
    content_hash: str


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    local_id: str
    qualified_name: str
    name: str
    kind: SymbolKind
    signature: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    parent_local_id: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedRelation:
    type: RelationType
    target_text: str
    source_local_id: str | None = None
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class ParsedFile:
    document: SourceDocument
    symbols: tuple[ParsedSymbol, ...]
    relations: tuple[ParsedRelation, ...]
    status: ParseStatus = ParseStatus.PARSED
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CodeChunk:
    id: str
    path: str
    language: Language
    content: str
    content_hash: str
    token_count: int
    start_line: int
    end_line: int
    part: int
    symbol_local_id: str | None = None
    symbol_name: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: CodeChunk
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    score: float
    path: str
    content: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    language: Language | None = None


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    languages: tuple[Language, ...] = ()
    path_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    original_query: str
    semantic_query: str
    intent: QueryIntent
    keywords: tuple[str, ...]
    symbols: tuple[str, ...]
    path_terms: tuple[str, ...]
    filters: RetrievalFilters


@dataclass(frozen=True, slots=True)
class RankedHit:
    hit: SearchHit
    rrf_score: float
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    repository_id: str
    index_version_id: str
    commit_sha: str
    path: str
    language: Language | None
    symbol: str | None
    start_line: int
    end_line: int
    content: str
    rrf_score: float
    rerank_score: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    repository_id: str
    index_version_id: str
    query: QueryAnalysis
    evidence: tuple[Evidence, ...]
    degraded_sources: tuple[str, ...]
    reranker_applied: bool
    timings_ms: dict[str, float]


@dataclass(frozen=True, slots=True)
class RunBudgets:
    max_steps: int = 8
    max_tool_calls: int = 8
    max_tokens: int = 12_000
    timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class AgentCitation:
    evidence_id: str
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    text: str
    citations: tuple[AgentCitation, ...]
    incomplete: bool = False


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    id: str
    repository_id: str
    index_version_id: str
    session_id: str | None
    workflow: WorkflowKind
    question: str
    status: AgentRunStatus
    budgets: RunBudgets
    steps_used: int = 0
    tool_calls_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    answer: AgentAnswer | None = None
    error_summary: str | None = None
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class RunEvent:
    sequence: int
    type: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    repository_id: str
    index_version_id: str
    layer: MemoryLayer
    scope_key: str
    content: str
    session_id: str | None = None
    run_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    source_hashes: tuple[str, ...] = ()
    stale: bool = False
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    repository_id: str
    index_version_id: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    data: dict[str, object]
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class IndexBuild:
    repository: RepositoryRecord
    job: IndexJobRecord
    version_id: str
    prepared: PreparedRepository
    files: tuple[ParsedFile, ...]
    chunks: tuple[CodeChunk, ...]
