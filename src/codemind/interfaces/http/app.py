"""FastAPI application factory and dependency lifecycle."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from codemind import __version__
from codemind.application.agent_runs import AgentRunService
from codemind.application.retrieval import HybridRetrievalService
from codemind.config import Settings, get_settings
from codemind.infrastructure.agent.langgraph_runtime import LangGraphWorkflowRuntime
from codemind.infrastructure.agent.tools import ReadOnlyCodeToolRegistry
from codemind.infrastructure.database import create_engine
from codemind.infrastructure.health import (
    DatabaseReadinessCheck,
    OllamaReadinessCheck,
    QdrantReadinessCheck,
    ReadinessCheck,
)
from codemind.infrastructure.indexes.bm25 import SqliteBm25Index
from codemind.infrastructure.indexes.qdrant import QdrantVectorIndex
from codemind.infrastructure.models import build_embedding_provider, build_llm_provider
from codemind.infrastructure.persistence.agent_store import SqlAgentRunStore
from codemind.infrastructure.persistence.memory_store import SqlMemoryStore
from codemind.infrastructure.persistence.store import SqlIndexStore
from codemind.interfaces.http.agent_routes import router as agent_router
from codemind.interfaces.http.demo_routes import router as demo_router
from codemind.interfaces.http.memory_routes import router as memory_router
from codemind.interfaces.http.middleware import TraceMiddleware
from codemind.interfaces.http.problems import install_exception_handlers
from codemind.interfaces.http.repository_routes import router as repository_router
from codemind.interfaces.http.routes import router as health_router
from codemind.interfaces.http.search_routes import router as search_router
from codemind.logging import configure_logging
from codemind.retrieval.analyzer import RuleQueryAnalyzer
from codemind.retrieval.fusion import ContextPacker, HeuristicCodeReranker

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _checkpoint_context(settings: Settings) -> AsyncGenerator[Any]:
    if settings.environment == "test":
        yield InMemorySaver()
        return
    async with AsyncPostgresSaver.from_conn_string(
        settings.checkpoint_database_url
    ) as checkpointer:
        await checkpointer.setup()
        yield checkpointer


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_engine(resolved_settings.database_url)
        qdrant = AsyncQdrantClient(url=resolved_settings.qdrant_url, check_compatibility=False)
        app.state.settings = resolved_settings
        app.state.engine = engine
        app.state.qdrant = qdrant
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlIndexStore(sessions)
        embedding = build_embedding_provider(resolved_settings)
        app.state.index_store = store
        app.state.retrieval_service = HybridRetrievalService(
            store=store,
            analyzer=RuleQueryAnalyzer(),
            embedding=embedding,
            vector_index=QdrantVectorIndex(
                qdrant,
                dimensions=embedding.dimensions,
                collection_name=resolved_settings.vector_collection_name,
            ),
            lexical_index=SqliteBm25Index(resolved_settings.lexical_index_dir),
            symbol_index=store,
            reranker=HeuristicCodeReranker(),
            context_packer=ContextPacker(
                token_budget=resolved_settings.context_token_budget,
                max_evidence=resolved_settings.context_max_evidence,
                max_per_path=resolved_settings.context_max_per_path,
            ),
            recall_limit=resolved_settings.retrieval_recall_limit,
            rerank_limit=resolved_settings.retrieval_rerank_limit,
            rerank_timeout_seconds=resolved_settings.reranker_timeout_seconds,
        )
        agent_store = SqlAgentRunStore(sessions)
        memory_store = SqlMemoryStore(sessions)
        app.state.agent_run_store = agent_store
        app.state.memory_store = memory_store
        readiness_checks: list[ReadinessCheck] = [
            DatabaseReadinessCheck(engine),
            QdrantReadinessCheck(qdrant),
        ]
        if resolved_settings.embedding_provider == "ollama":
            readiness_checks.append(
                OllamaReadinessCheck(
                    resolved_settings.embedding_base_url,
                    min(
                        resolved_settings.embedding_timeout_seconds,
                        resolved_settings.readiness_timeout_seconds,
                    ),
                )
            )
        app.state.readiness_checks = readiness_checks
        async with _checkpoint_context(resolved_settings) as checkpointer:
            runtime = LangGraphWorkflowRuntime(
                store=agent_store,
                tools=ReadOnlyCodeToolRegistry(sessions, app.state.retrieval_service),
                llm=build_llm_provider(resolved_settings),
                checkpointer=checkpointer,
                memory=memory_store,
                node_delay_seconds=resolved_settings.agent_node_delay_seconds,
            )
            agent_service = AgentRunService(agent_store, runtime)
            app.state.agent_run_service = agent_service
            if (
                resolved_settings.agent_resume_on_startup
                and resolved_settings.environment != "test"
            ):
                await agent_service.resume_pending()
            await logger.ainfo("application_started", environment=resolved_settings.environment)
            try:
                yield
            finally:
                await agent_service.close()
                await logger.ainfo("application_stopped")
        await qdrant.close()
        await engine.dispose()

    app = FastAPI(
        title="CodeMind API",
        summary="Repository-level code understanding RAG Agent",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(TraceMiddleware)
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(demo_router)
    app.include_router(repository_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    return app
