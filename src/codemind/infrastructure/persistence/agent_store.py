"""Durable product state for Agent runs, steps, and SSE events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codemind.application.ports import AgentRunStore
from codemind.domain.models import (
    AgentAnswer,
    AgentCitation,
    AgentRunRecord,
    AgentRunStatus,
    RunBudgets,
    RunEvent,
    WorkflowKind,
)
from codemind.infrastructure.persistence.models import (
    AgentEventModel,
    AgentRunModel,
    AgentStepModel,
    RepositoryModel,
)


class SqlAgentRunStore(AgentRunStore):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_run(
        self,
        repository_id: str,
        workflow: WorkflowKind,
        question: str,
        session_id: str | None,
        budgets: RunBudgets,
    ) -> AgentRunRecord:
        async with self._sessions.begin() as session:
            repository = await session.get(RepositoryModel, repository_id)
            if repository is None:
                raise LookupError("repository_not_found")
            if repository.active_index_version_id is None:
                raise RuntimeError("repository_not_ready")
            model = AgentRunModel(
                id=str(uuid4()),
                repository_id=repository_id,
                index_version_id=repository.active_index_version_id,
                session_id=session_id,
                workflow=workflow.value,
                question=question,
                status=AgentRunStatus.QUEUED.value,
                max_steps=budgets.max_steps,
                max_tool_calls=budgets.max_tool_calls,
                max_tokens=budgets.max_tokens,
                timeout_seconds=budgets.timeout_seconds,
                steps_used=0,
                tool_calls_used=0,
                input_tokens=0,
                output_tokens=0,
                cancel_requested=False,
            )
            session.add(model)
            await session.flush()
        return self._record(model)

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        async with self._sessions() as session:
            model = await session.get(AgentRunModel, run_id)
            return self._record(model) if model else None

    async def list_resumable_runs(self) -> list[AgentRunRecord]:
        statement = select(AgentRunModel).where(
            AgentRunModel.status.in_([AgentRunStatus.QUEUED.value, AgentRunStatus.RUNNING.value])
        )
        async with self._sessions() as session:
            return [self._record(model) for model in await session.scalars(statement)]

    async def is_cancel_requested(self, run_id: str) -> bool:
        async with self._sessions() as session:
            value = await session.scalar(
                select(AgentRunModel.cancel_requested).where(AgentRunModel.id == run_id)
            )
            return bool(value)

    async def request_cancel(self, run_id: str) -> AgentRunRecord | None:
        async with self._sessions.begin() as session:
            model = await session.get(AgentRunModel, run_id)
            if model is None:
                return None
            if model.status in {
                AgentRunStatus.COMPLETED.value,
                AgentRunStatus.PARTIAL.value,
                AgentRunStatus.FAILED.value,
                AgentRunStatus.CANCELLED.value,
            }:
                return self._record(model)
            model.cancel_requested = True
            await session.flush()
            return self._record(model)

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
    ) -> None:
        async with self._sessions.begin() as session:
            model = await session.get(AgentRunModel, run_id)
            if model is None:
                return
            model.status = status.value
            if status is AgentRunStatus.RUNNING and model.started_at is None:
                model.started_at = datetime.now(UTC)
            if status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.PARTIAL,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            }:
                model.finished_at = datetime.now(UTC)
            if answer is not None:
                model.answer_json = self._answer_json(answer)
            if error_summary is not None:
                model.error_summary = error_summary[:2_000]
            if steps_used is not None:
                model.steps_used = steps_used
            if tool_calls_used is not None:
                model.tool_calls_used = tool_calls_used
            if input_tokens is not None:
                model.input_tokens = input_tokens
            if output_tokens is not None:
                model.output_tokens = output_tokens

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
    ) -> None:
        async with self._sessions.begin() as session:
            model = await session.scalar(
                select(AgentStepModel).where(
                    AgentStepModel.run_id == run_id,
                    AgentStepModel.node_name == node_name,
                )
            )
            if model is None:
                model = AgentStepModel(
                    id=str(uuid4()),
                    run_id=run_id,
                    node_name=node_name,
                    sequence=sequence,
                    status=status,
                )
                session.add(model)
            model.sequence = sequence
            model.status = status
            model.input_summary = input_summary
            model.output_summary = output_summary
            model.error_summary = error_summary
            if status in {"completed", "failed", "cancelled", "partial"}:
                model.finished_at = datetime.now(UTC)

    async def append_event(
        self,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        data: dict[str, object],
    ) -> RunEvent:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(AgentEventModel).where(
                    AgentEventModel.run_id == run_id,
                    AgentEventModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._event(existing)
            last = await session.scalar(
                select(func.max(AgentEventModel.sequence)).where(AgentEventModel.run_id == run_id)
            )
            model = AgentEventModel(
                id=str(uuid4()),
                run_id=run_id,
                sequence=int(last or 0) + 1,
                idempotency_key=idempotency_key,
                type=event_type,
                data_json=data,
            )
            session.add(model)
            await session.flush()
            return self._event(model)

    async def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        statement = (
            select(AgentEventModel)
            .where(
                AgentEventModel.run_id == run_id,
                AgentEventModel.sequence > after_sequence,
            )
            .order_by(AgentEventModel.sequence)
        )
        async with self._sessions() as session:
            return [self._event(model) for model in await session.scalars(statement)]

    @classmethod
    def _record(cls, model: AgentRunModel) -> AgentRunRecord:
        return AgentRunRecord(
            id=model.id,
            repository_id=model.repository_id,
            index_version_id=model.index_version_id,
            session_id=model.session_id,
            workflow=WorkflowKind(model.workflow),
            question=model.question,
            status=AgentRunStatus(model.status),
            budgets=RunBudgets(
                max_steps=model.max_steps,
                max_tool_calls=model.max_tool_calls,
                max_tokens=model.max_tokens,
                timeout_seconds=model.timeout_seconds,
            ),
            steps_used=model.steps_used,
            tool_calls_used=model.tool_calls_used,
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
            answer=cls._answer(model.answer_json),
            error_summary=model.error_summary,
            cancel_requested=model.cancel_requested,
        )

    @staticmethod
    def _answer_json(answer: AgentAnswer) -> dict[str, object]:
        return {
            "text": answer.text,
            "incomplete": answer.incomplete,
            "citations": [
                {
                    "evidence_id": citation.evidence_id,
                    "path": citation.path,
                    "start_line": citation.start_line,
                    "end_line": citation.end_line,
                }
                for citation in answer.citations
            ],
        }

    @staticmethod
    def _answer(value: dict[str, object] | None) -> AgentAnswer | None:
        if value is None:
            return None
        citations = cast(list[dict[str, Any]], value.get("citations", []))
        return AgentAnswer(
            text=str(value.get("text", "")),
            incomplete=bool(value.get("incomplete", False)),
            citations=tuple(
                AgentCitation(
                    evidence_id=str(item["evidence_id"]),
                    path=str(item["path"]),
                    start_line=int(item["start_line"]),
                    end_line=int(item["end_line"]),
                )
                for item in citations
            ),
        )

    @staticmethod
    def _event(model: AgentEventModel) -> RunEvent:
        return RunEvent(model.sequence, model.type, dict(model.data_json))
