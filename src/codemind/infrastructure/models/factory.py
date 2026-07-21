"""Provider composition selected by validated application settings."""

from __future__ import annotations

from codemind.agent.grounding import TemplateGroundedLLM
from codemind.application.ports import EmbeddingProvider, LLMProvider
from codemind.config import Settings
from codemind.indexing.embedding import HashEmbeddingProvider
from codemind.infrastructure.models.ollama import (
    FallbackLLMProvider,
    OllamaEmbeddingProvider,
    OllamaGroundedLLM,
)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddingProvider(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.embedding_timeout_seconds,
            query_instruction=settings.embedding_query_instruction,
        )
    return HashEmbeddingProvider(settings.embedding_dimensions)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "ollama":
        provider: LLMProvider = OllamaGroundedLLM(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            context_window=settings.llm_context_window,
            max_output_tokens=settings.llm_max_output_tokens,
        )
        if settings.llm_fallback_to_template:
            return FallbackLLMProvider(provider)
        return provider
    return TemplateGroundedLLM()
