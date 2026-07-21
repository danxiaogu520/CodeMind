from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from codemind.application.indexing import IndexingService
from codemind.application.memory import compact_working_memory
from codemind.domain.models import (
    AgentAnswer,
    AgentCitation,
    EmbeddedChunk,
    Evidence,
    JobStatus,
    Language,
    ParsedFile,
    PreparedRepository,
    RetrievalFilters,
    RunBudgets,
    SearchHit,
    SourceDocument,
    SourceType,
    WorkflowKind,
)
from codemind.indexing.chunker import AstCodeChunker
from codemind.infrastructure.database import Base
from codemind.infrastructure.persistence.agent_store import SqlAgentRunStore
from codemind.infrastructure.persistence.memory_store import SqlMemoryStore
from codemind.infrastructure.persistence.store import SqlIndexStore
from codemind.parsing.tree_sitter_parser import TreeSitterCodeParser


@event.listens_for(Engine, "connect")
def enable_phase4_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    del connection_record
    connection = cast(sqlite3.Connection, dbapi_connection)
    connection.execute("PRAGMA foreign_keys=ON")


def document(path: str, content: str) -> SourceDocument:
    raw = content.encode()
    return SourceDocument(path, Language.PYTHON, raw, sha256(raw).hexdigest())


class MutableSource:
    commit = "commit-1"

    async def prepare(
        self, repository_id: str, source_type: SourceType, source_uri: str, ref: str | None
    ) -> PreparedRepository:
        del repository_id, source_type, source_uri, ref
        return PreparedRepository(Path("/fixture"), self.commit)

    async def cleanup(self, prepared: PreparedRepository) -> None:
        del prepared


class MutableDiscoverer:
    documents: list[SourceDocument]

    def __init__(self, documents: list[SourceDocument]) -> None:
        self.documents = documents

    async def discover(self, root: Path) -> list[SourceDocument]:
        del root
        return self.documents


class CountingParser:
    def __init__(self) -> None:
        self.count = 0
        self._parser = TreeSitterCodeParser()

    def parse(self, document: SourceDocument) -> ParsedFile:
        self.count += 1
        return self._parser.parse(document)


class SelectiveFailingParser:
    def __init__(self) -> None:
        self._parser = TreeSitterCodeParser()

    def parse(self, document: SourceDocument) -> ParsedFile:
        if document.path.endswith("bad.py"):
            raise RuntimeError("native parser process exited")
        return self._parser.parse(document)


class CountingEmbedding:
    model_id = "counting-v1"
    dimensions = 2

    def __init__(self) -> None:
        self.text_count = 0

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.text_count += len(texts)
        return [(1.0, 0.0) for _ in texts]


class FakeVectorIndex:
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[EmbeddedChunk]
    ) -> None:
        del repository_id, version_id, chunks

    async def build_incremental_version(
        self,
        repository_id: str,
        version_id: str,
        base_version_id: str,
        unchanged_paths: Sequence[str],
        changed_chunks: Sequence[EmbeddedChunk],
    ) -> None:
        del repository_id, version_id, base_version_id, unchanged_paths, changed_chunks

    async def delete_version(self, version_id: str) -> None:
        del version_id

    async def search(
        self,
        repository_id: str,
        version_id: str,
        vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        del repository_id, version_id, vector, limit, filters
        return []


class FakeLexicalIndex:
    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[object]
    ) -> None:
        del repository_id, version_id, chunks

    async def delete_version(self, version_id: str) -> None:
        del version_id

    async def search(
        self,
        repository_id: str,
        version_id: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        del repository_id, version_id, query, limit, filters
        return []


async def test_incremental_index_only_parses_and_embeds_changed_files(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'incremental.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlIndexStore(sessions)
    source = MutableSource()
    discoverer = MutableDiscoverer(
        [document("src/a.py", "def a(): return 1\n"), document("src/b.py", "def b(): return 2\n")]
    )
    parser = CountingParser()
    embedding = CountingEmbedding()
    service = IndexingService(
        store=store,
        source=source,
        discoverer=discoverer,
        parser=parser,
        chunker=AstCodeChunker(),
        embedding=embedding,
        vector_index=FakeVectorIndex(),
        lexical_index=FakeLexicalIndex(),  # type: ignore[arg-type]
    )
    repository, _ = await store.create_repository("sample", SourceType.LOCAL, "/fixture", None)
    first = await service.claim_job()
    assert first is not None
    await service.process(first)
    assert parser.count == 2
    first_active = await store.get_repository(repository.id)
    assert first_active is not None and first_active.active_index_version_id is not None
    first_version_id = first_active.active_index_version_id

    source.commit = "commit-2"
    discoverer.documents = [
        document("src/a.py", "def a(): return 42\n"),
        document("src/b.py", "def b(): return 2\n"),
    ]
    parser.count = 0
    embedding.text_count = 0
    queued = await store.enqueue_job(repository.id, None)
    second = await service.claim_job()
    assert second is not None
    assert second.id == queued.id
    await service.process(second)

    completed = await store.get_job(second.id)
    active = await store.get_repository(repository.id)
    assert completed is not None
    assert active is not None and active.active_index_version_id is not None
    assert completed.status is JobStatus.COMPLETED
    assert parser.count == 1
    assert completed.delta.files_changed == 1
    assert completed.delta.files_reused == 1
    assert completed.delta.chunks_embedded == embedding.text_count
    assert completed.delta.chunks_reused > 0
    manifest = await store.get_file_manifest(active.active_index_version_id)
    assert manifest["src/b.py"] == document("src/b.py", "def b(): return 2\n").content_hash

    source.commit = "commit-3"
    discoverer.documents = [document("src/a.py", "def a(): return 42\n")]
    parser.count = 0
    embedding.text_count = 0
    deleted_job = await store.enqueue_job(repository.id, None)
    claimed_deleted = await service.claim_job()
    assert claimed_deleted is not None
    await service.process(claimed_deleted)
    deleted_result = await store.get_job(deleted_job.id)
    assert deleted_result is not None
    assert deleted_result.delta.files_deleted == 1
    assert deleted_result.delta.files_reused == 1
    assert deleted_result.delta.chunks_embedded == 0
    assert parser.count == 0
    assert embedding.text_count == 0

    source.commit = "commit-4"
    fourth_job = await store.enqueue_job(repository.id, None)
    claimed_fourth = await service.claim_job()
    assert claimed_fourth is not None
    await service.process(claimed_fourth)
    assert (await store.get_job(fourth_job.id)) is not None
    assert await store.get_version_commit(first_version_id) is None

    embedding.model_id = "counting-v2"
    source.commit = "commit-5"
    parser.count = 0
    embedding.text_count = 0
    model_change_job = await store.enqueue_job(repository.id, None)
    claimed_model_change = await service.claim_job()
    assert claimed_model_change is not None
    await service.process(claimed_model_change)
    model_change_result = await store.get_job(model_change_job.id)
    assert model_change_result is not None
    assert model_change_result.delta.files_reused == 0
    assert model_change_result.delta.chunks_reused == 0
    assert parser.count == 1
    assert embedding.text_count > 0
    await engine.dispose()


async def test_indexing_degrades_one_parser_failure_without_failing_job(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'parse-fallback.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlIndexStore(sessions)
    discoverer = MutableDiscoverer(
        [
            document("src/good.py", "def good(): return 1\n"),
            document("src/bad.py", "def bad(): return 2\n"),
        ]
    )
    service = IndexingService(
        store=store,
        source=MutableSource(),
        discoverer=discoverer,
        parser=SelectiveFailingParser(),
        chunker=AstCodeChunker(),
        embedding=CountingEmbedding(),
        vector_index=FakeVectorIndex(),
        lexical_index=FakeLexicalIndex(),  # type: ignore[arg-type]
    )
    repository, _ = await store.create_repository("sample", SourceType.LOCAL, "/fixture", None)
    job = await service.claim_job()
    assert job is not None

    await service.process(job)

    completed = await store.get_job(job.id)
    active = await store.get_repository(repository.id)
    assert completed is not None and completed.status is JobStatus.PARTIAL
    assert completed.warnings == 1
    assert active is not None and active.active_index_version_id is not None
    chunks = await store.list_chunks(active.active_index_version_id)
    assert {chunk.path for chunk in chunks} == {"src/good.py", "src/bad.py"}
    await engine.dispose()


async def test_memory_is_session_isolated_and_invalidated_by_sources(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    index_store = SqlIndexStore(sessions)
    agent_store = SqlAgentRunStore(sessions)
    memory = SqlMemoryStore(sessions)
    repository, _ = await index_store.create_repository("sample", SourceType.LOCAL, "/x", None)
    parser = TreeSitterCodeParser()
    chunker = AstCodeChunker()
    source = document("src/auth.py", "def login(): return token()\n")
    parsed = parser.parse(source)
    chunks = chunker.chunk(repository.id, parsed)
    v1 = await index_store.create_version(repository.id, "v1", "parser", "embedding")
    await index_store.persist_parsed_files(v1, [parsed], chunks)
    await index_store.activate_version(repository.id, v1)
    await memory.refresh_project(repository.id, v1)
    run = await agent_store.create_run(
        repository.id,
        WorkflowKind.ANSWER_QUESTION,
        "Where is login?",
        "session-a",
        RunBudgets(),
    )
    evidence = Evidence(
        "ev-1",
        repository.id,
        v1,
        "v1",
        source.path,
        Language.PYTHON,
        "login",
        1,
        1,
        source.content.decode(),
        1.0,
        1.0,
        ("dense",),
    )
    answer = AgentAnswer("login is in auth.py", (AgentCitation("ev-1", source.path, 1, 1),))
    await memory.record_run(run, answer, [evidence])

    own = await memory.recall(repository.id, v1, "login", "session-a")
    other = await memory.recall(repository.id, v1, "login", "session-b")
    assert any(item.layer.value == "session" for item in own)
    assert all(item.layer.value != "session" for item in other)

    v2 = await index_store.create_version(repository.id, "v2", "parser", "embedding")
    await index_store.persist_parsed_files(v2, [parsed], chunks)
    reused, invalidated = await memory.rebase_version(repository.id, v1, v2, [])
    assert reused == 2
    assert invalidated >= 1
    assert any(
        item.layer.value == "episodic"
        for item in await memory.recall(repository.id, v2, "login", "session-a")
    )

    v3 = await index_store.create_version(repository.id, "v3", "parser", "embedding")
    await index_store.persist_parsed_files(v3, [parsed], chunks)
    reused, invalidated = await memory.rebase_version(repository.id, v2, v3, [source.path])
    assert reused == 0
    assert invalidated == 2
    assert await memory.recall(repository.id, v3, "login", "session-a") == []
    await engine.dispose()


def test_working_memory_compaction_preserves_all_evidence_ids() -> None:
    observations = [{"tool": f"tool-{index}", "value": index} for index in range(8)]
    compacted, summary = compact_working_memory(observations, ["ev-1", "ev-2", "ev-1"])

    assert summary is not None
    assert summary["evidence_ids"] == ["ev-1", "ev-2"]
    assert len(compacted) == 5
