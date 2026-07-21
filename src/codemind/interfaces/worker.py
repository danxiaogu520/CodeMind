"""Database-backed index worker process composition root."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from codemind.application.indexing import IndexingService
from codemind.config import Settings, get_settings
from codemind.indexing.chunker import AstCodeChunker
from codemind.infrastructure.database import create_engine
from codemind.infrastructure.indexes.bm25 import SqliteBm25Index
from codemind.infrastructure.indexes.qdrant import QdrantVectorIndex
from codemind.infrastructure.models import build_embedding_provider
from codemind.infrastructure.persistence.memory_store import SqlMemoryStore
from codemind.infrastructure.persistence.store import SqlIndexStore
from codemind.ingestion.discovery import SourceDiscoverer
from codemind.ingestion.sources import SafeRepositorySource
from codemind.logging import configure_logging
from codemind.parsing.isolated_parser import IsolatedCodeParser

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def build_service(settings: Settings) -> AsyncGenerator[IndexingService]:
    engine = create_engine(settings.database_url)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    embedding = build_embedding_provider(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlIndexStore(sessions)
    parser = IsolatedCodeParser(timeout_seconds=settings.parser_timeout_seconds)
    service = IndexingService(
        store=store,
        source=SafeRepositorySource(
            settings.repository_work_dir,
            settings.allowed_repository_roots,
            git_timeout_seconds=settings.git_timeout_seconds,
            git_allowed_hosts=settings.git_allowed_hosts,
        ),
        discoverer=SourceDiscoverer(max_file_bytes=settings.repository_max_file_bytes),
        parser=parser,
        chunker=AstCodeChunker(),
        embedding=embedding,
        vector_index=QdrantVectorIndex(
            qdrant,
            dimensions=embedding.dimensions,
            collection_name=settings.vector_collection_name,
        ),
        lexical_index=SqliteBm25Index(settings.lexical_index_dir),
        memory=SqlMemoryStore(sessions),
        retained_versions=settings.index_retained_versions,
    )
    try:
        yield service
    finally:
        parser.close()
        await qdrant.close()
        await engine.dispose()


async def run_worker(settings: Settings, *, once: bool = False) -> None:
    configure_logging(settings)
    async with build_service(settings) as service:
        await logger.ainfo("index_worker_started", once=once)
        next_recovery_at = 0.0
        while True:
            try:
                loop_time = asyncio.get_running_loop().time()
                if loop_time >= next_recovery_at:
                    recovered, failed = await service.recover_stale_jobs(
                        stale_after_seconds=settings.index_job_stale_after_seconds,
                        max_attempts=settings.index_job_max_attempts,
                    )
                    if recovered or failed:
                        await logger.awarning(
                            "stale_index_jobs_recovered",
                            requeued=recovered,
                            failed=failed,
                        )
                    next_recovery_at = loop_time + min(
                        30.0, settings.index_job_stale_after_seconds / 2
                    )
                job = await service.claim_job()
            except Exception:
                await logger.aexception("index_worker_claim_failed")
                if once:
                    raise
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue
            if job is None:
                if once:
                    return
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue
            await service.process(job)
            if once:
                return


def main() -> None:
    asyncio.run(run_worker(get_settings()))
