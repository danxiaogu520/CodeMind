from __future__ import annotations

from collections.abc import Sequence

from codemind.domain.models import AgentAnswer, Evidence, Language, WorkflowKind
from codemind.infrastructure.models.http import ModelServiceError
from codemind.infrastructure.models.ollama import (
    FallbackLLMProvider,
    OllamaEmbeddingProvider,
    OllamaGroundedLLM,
)


def _evidence() -> Evidence:
    return Evidence(
        id="ev_safe",
        repository_id="repo",
        index_version_id="version",
        commit_sha="commit",
        path="src/auth.py",
        language=Language.PYTHON,
        symbol="login",
        start_line=4,
        end_line=8,
        content="def login(): return create_token()",
        rrf_score=0.1,
        rerank_score=1.0,
        reasons=("dense",),
    )


async def test_ollama_embedding_validates_and_normalizes_vectors() -> None:
    requests: list[dict[str, object]] = []

    async def post(url: str, payload: dict[str, object], timeout_seconds: float) -> object:
        del url, timeout_seconds
        requests.append(payload)
        return {"embeddings": [[3.0, 4.0, 0.0, 0.0]]}

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama:11434",
        model="qwen3-embedding:0.6b",
        dimensions=4,
        timeout_seconds=1,
        query_instruction="retrieve code",
        post=post,
    )

    vector = await provider.embed_query("登录在哪里")

    assert vector == (0.6, 0.8, 0.0, 0.0)
    assert requests[0]["input"] == ["Instruct: retrieve code\nQuery: 登录在哪里"]


async def test_ollama_embedding_rejects_wrong_dimensions() -> None:
    async def post(url: str, payload: dict[str, object], timeout_seconds: float) -> object:
        del url, payload, timeout_seconds
        return {"embeddings": [[1.0, 2.0]]}

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama:11434",
        model="embedding",
        dimensions=4,
        timeout_seconds=1,
        query_instruction="",
        post=post,
    )

    try:
        await provider.embed(["code"])
    except ModelServiceError as exc:
        assert "dimension mismatch" in str(exc)
    else:
        raise AssertionError("Expected an invalid embedding dimension to fail.")


async def test_ollama_llm_maps_only_supplied_citation_ids() -> None:
    async def post(url: str, payload: dict[str, object], timeout_seconds: float) -> object:
        del url, payload, timeout_seconds
        return {
            "message": {
                "content": (
                    '{"answer":"登录函数调用令牌创建。",'
                    '"citation_ids":["ev_safe","ev_made_up"],"incomplete":false}'
                )
            }
        }

    provider = OllamaGroundedLLM(
        base_url="http://ollama:11434",
        model="qwen3:8b",
        timeout_seconds=1,
        context_window=8192,
        max_output_tokens=512,
        post=post,
    )

    answer = await provider.generate_grounded_answer(
        WorkflowKind.ANSWER_QUESTION, "登录在哪里", [_evidence()], []
    )

    assert answer.text == "登录函数调用令牌创建。"
    assert [item.evidence_id for item in answer.citations] == ["ev_safe"]
    assert not answer.incomplete


class _BrokenLLM:
    model_id = "broken"

    async def generate_grounded_answer(
        self,
        workflow: WorkflowKind,
        question: str,
        evidence: Sequence[Evidence],
        observations: Sequence[dict[str, object]],
    ) -> AgentAnswer:
        del workflow, question, evidence, observations
        raise ModelServiceError("offline")


async def test_llm_fallback_is_marked_incomplete() -> None:
    provider = FallbackLLMProvider(_BrokenLLM())

    answer = await provider.generate_grounded_answer(
        WorkflowKind.ANSWER_QUESTION, "where", [_evidence()], []
    )

    assert answer.incomplete
    assert answer.citations[0].evidence_id == "ev_safe"
