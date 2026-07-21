"""Validated application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """CodeMind settings loaded from environment variables and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CODEMIND_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    database_url: str = "postgresql+asyncpg://codemind:codemind@localhost:5432/codemind"
    checkpoint_database_url: str = "postgresql://codemind:codemind@localhost:5432/codemind"
    qdrant_url: str = "http://localhost:6333"
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    repository_work_dir: Path = Path("data/repositories")
    allowed_repository_roots: list[Path] = Field(default_factory=lambda: list[Path]())
    repository_max_file_bytes: int = Field(default=1_000_000, ge=1024)
    git_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    git_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["github.com", "gitlab.com", "bitbucket.org"]
    )
    embedding_provider: Literal["hash", "ollama"] = "hash"
    embedding_model: str = Field(default="qwen3-embedding:0.6b", min_length=1)
    embedding_base_url: str = "http://localhost:11434"
    embedding_dimensions: int = Field(default=256, ge=32, le=4096)
    embedding_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    embedding_query_instruction: str = (
        "Given a software repository question, retrieve relevant source code that answers it."
    )
    vector_collection_name: str = Field(
        default="codemind_code_chunks", pattern=r"^[A-Za-z0-9_-]{1,200}$"
    )
    lexical_index_dir: Path = Path("data/indexes")
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    parser_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    index_job_stale_after_seconds: float = Field(default=120.0, gt=10, le=3600)
    index_job_max_attempts: int = Field(default=3, ge=1, le=20)

    retrieval_recall_limit: int = Field(default=40, ge=5, le=200)
    retrieval_rerank_limit: int = Field(default=30, ge=1, le=100)
    reranker_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    context_token_budget: int = Field(default=6_000, ge=256, le=100_000)
    context_max_evidence: int = Field(default=20, ge=1, le=100)
    context_max_per_path: int = Field(default=3, ge=1, le=20)
    llm_provider: Literal["template", "ollama"] = "template"
    llm_model: str = Field(default="qwen3:8b", min_length=1)
    llm_base_url: str = "http://localhost:11434"
    llm_timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    llm_context_window: int = Field(default=8_192, ge=2_048, le=131_072)
    llm_max_output_tokens: int = Field(default=1_024, ge=128, le=8_192)
    llm_fallback_to_template: bool = True
    agent_resume_on_startup: bool = True
    agent_node_delay_seconds: float = Field(default=0.0, ge=0, le=10)
    index_retained_versions: int = Field(default=3, ge=1, le=20)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
