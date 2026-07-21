"""Relational persistence schema for repository knowledge bases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from codemind.infrastructure.database import Base


class RepositoryModel(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(20))
    source_uri: Mapped[str] = mapped_column(Text)
    default_ref: Mapped[str | None] = mapped_column(String(255))
    active_index_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IndexVersionModel(Base):
    __tablename__ = "index_versions"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", "parser_version", "embedding_model"),
        Index("ix_index_versions_repository_status", "repository_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndexJobModel(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (Index("ix_index_jobs_claim", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    target_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(50))
    processed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delta_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class SourceFileModel(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("version_id", "path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("index_versions.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(30))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(20))
    error_summary: Mapped[str | None] = mapped_column(Text)


class SymbolModel(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        Index("ix_symbols_file_qualified", "file_id", "qualified_name"),
        Index("ix_symbols_short_name", "short_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    qualified_name: Mapped[str] = mapped_column(String(1000))
    short_name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(30))
    signature: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)


class CodeChunkModel(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        Index("ix_code_chunks_version_path", "version_id", "path"),
        Index("ix_code_chunks_chunk_id", "chunk_id"),
    )

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(36))
    version_id: Mapped[str] = mapped_column(
        ForeignKey("index_versions.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(30))
    part: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)


class RelationModel(Base):
    __tablename__ = "relations"
    __table_args__ = (Index("ix_relations_version_type", "version_id", "type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("index_versions.id", ondelete="CASCADE"), index=True
    )
    source_symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True
    )
    target_symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), index=True
    )
    target_text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)


class AgentRunModel(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    index_version_id: Mapped[str] = mapped_column(
        ForeignKey("index_versions.id", ondelete="RESTRICT"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(100), index=True)
    workflow: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    max_steps: Mapped[int] = mapped_column(Integer)
    max_tool_calls: Mapped[int] = mapped_column(Integer)
    max_tokens: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    answer_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_summary: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStepModel(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "node_name"),
        Index("ix_agent_steps_run_sequence", "run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    node_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentEventModel(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("run_id", "idempotency_key", name="uq_agent_events_idempotency"),
        UniqueConstraint("run_id", "sequence", name="uq_agent_events_sequence"),
        Index("ix_agent_events_run_sequence", "run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(80))
    data_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryModel(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "index_version_id",
            "layer",
            "scope_key",
            "session_key",
            name="uq_memories_scope",
        ),
        Index("ix_memories_recall", "repository_id", "index_version_id", "layer", "stale"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    index_version_id: Mapped[str] = mapped_column(
        ForeignKey("index_versions.id", ondelete="CASCADE"), index=True
    )
    layer: Mapped[str] = mapped_column(String(20))
    scope_key: Mapped[str] = mapped_column(String(300))
    session_id: Mapped[str | None] = mapped_column(String(100), index=True)
    session_key: Mapped[str] = mapped_column(String(100), default="")
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_hashes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
