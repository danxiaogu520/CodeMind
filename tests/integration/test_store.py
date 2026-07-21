from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from codemind.domain.models import (
    AgentAnswer,
    AgentCitation,
    AgentRunStatus,
    JobStatus,
    Language,
    RunBudgets,
    SourceDocument,
    SourceType,
    WorkflowKind,
)
from codemind.indexing.chunker import AstCodeChunker
from codemind.infrastructure.database import Base
from codemind.infrastructure.persistence.agent_store import SqlAgentRunStore
from codemind.infrastructure.persistence.models import (
    CodeChunkModel,
    IndexJobModel,
    RelationModel,
    SourceFileModel,
    SymbolModel,
)
from codemind.infrastructure.persistence.store import SqlIndexStore
from codemind.parsing.tree_sitter_parser import TreeSitterCodeParser


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    del connection_record
    connection = cast(sqlite3.Connection, dbapi_connection)
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def test_repository_job_lifecycle_with_sql_store(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'store.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SqlIndexStore(async_sessionmaker(engine, expire_on_commit=False))

    repository, queued = await store.create_repository(
        "sample", SourceType.LOCAL, "/repositories/sample", "main"
    )
    claimed = await store.claim_job()

    assert queued.status is JobStatus.QUEUED
    assert claimed is not None
    assert claimed.id == queued.id
    assert claimed.status is JobStatus.RUNNING
    await store.update_job(claimed.id, status=JobStatus.COMPLETED, stage="completed")
    completed = await store.get_job(claimed.id)
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED
    assert (await store.get_repository(repository.id)) == repository
    await engine.dispose()


async def test_stale_job_is_requeued_and_failed_version_can_be_rebuilt(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlIndexStore(sessions)
    repository, queued = await store.create_repository(
        "sample", SourceType.LOCAL, "/repositories/sample", "main"
    )
    claimed = await store.claim_job()
    assert claimed is not None
    version_id = await store.create_version(repository.id, "recoverable", "tree-sitter-v2", "fake")
    async with sessions.begin() as session:
        job = await session.get(IndexJobModel, queued.id)
        assert job is not None
        job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)

    recovered, failed = await store.recover_stale_jobs(
        datetime.now(UTC) - timedelta(minutes=2), max_attempts=3
    )
    retried = await store.claim_job()
    rebuilt_version_id = await store.create_version(
        repository.id, "recoverable", "tree-sitter-v2", "fake"
    )

    assert (recovered, failed) == (1, 0)
    assert retried is not None and retried.id == queued.id
    assert retried.status is JobStatus.RUNNING
    assert rebuilt_version_id == version_id
    await engine.dispose()


async def test_persist_and_activate_complete_parsed_graph(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlIndexStore(sessions)
    repository, _ = await store.create_repository(
        "sample", SourceType.LOCAL, "/repositories/sample", "main"
    )
    version_id = await store.create_version(repository.id, "abc123", "tree-sitter-v1", "fake")
    content = b"def issue_token(user):\n    return sign(user)\n"
    document = SourceDocument(
        path="src/token.py",
        language=Language.PYTHON,
        content=content,
        content_hash=sha256(content).hexdigest(),
    )
    parsed = TreeSitterCodeParser().parse(document)
    chunks = AstCodeChunker().chunk(repository.id, parsed)

    await store.persist_parsed_files(version_id, [parsed], chunks)
    await store.activate_version(repository.id, version_id)

    async with sessions() as session:
        counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (SourceFileModel, SymbolModel, CodeChunkModel, RelationModel)
        ]
    active = await store.get_repository(repository.id)
    symbol_hits = await store.search_symbols(repository.id, version_id, ["issue_token"], 10)
    assert counts == [1, 1, 1, 1]
    assert symbol_hits[0].symbol_name == "issue_token"
    assert active is not None
    assert active.active_index_version_id == version_id
    await engine.dispose()


async def test_agent_run_store_persists_answer_steps_and_idempotent_events(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    index_store = SqlIndexStore(sessions)
    repository, _ = await index_store.create_repository(
        "sample", SourceType.LOCAL, "/sample", "main"
    )
    version_id = await index_store.create_version(repository.id, "abc", "parser", "embedding")
    await index_store.activate_version(repository.id, version_id)
    store = SqlAgentRunStore(sessions)
    run = await store.create_run(
        repository.id,
        WorkflowKind.ANSWER_QUESTION,
        "Where is login?",
        "session-1",
        RunBudgets(),
    )
    await store.record_step(run.id, "retrieve", 1, "completed", output_summary="one hit")
    first = await store.append_event(run.id, "retrieve.done", "tool.completed", {"count": 1})
    repeated = await store.append_event(run.id, "retrieve.done", "tool.completed", {"count": 999})
    answer = AgentAnswer("found", (AgentCitation("ev", "src/a.py", 1, 2),))
    await store.mark_run(
        run.id,
        AgentRunStatus.COMPLETED,
        answer=answer,
        steps_used=1,
        tool_calls_used=1,
    )

    saved = await store.get_run(run.id)
    events = await store.list_events(run.id)
    assert first == repeated
    assert events == [first]
    assert saved is not None
    assert saved.status is AgentRunStatus.COMPLETED
    assert saved.answer == answer
    assert saved.index_version_id == version_id
    await engine.dispose()
