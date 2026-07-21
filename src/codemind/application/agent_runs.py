"""Agent Run lifecycle independent of the workflow framework."""

from __future__ import annotations

import asyncio

import structlog

from codemind.application.ports import AgentRunStore, WorkflowRuntime
from codemind.domain.models import (
    AgentRunRecord,
    AgentRunStatus,
    RunBudgets,
    WorkflowKind,
)

logger = structlog.get_logger(__name__)


class AgentRunService:
    def __init__(self, store: AgentRunStore, runtime: WorkflowRuntime) -> None:
        self._store = store
        self._runtime = runtime
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_run(
        self,
        repository_id: str,
        workflow: WorkflowKind,
        question: str,
        session_id: str | None,
        budgets: RunBudgets,
    ) -> AgentRunRecord:
        run = await self._store.create_run(repository_id, workflow, question, session_id, budgets)
        await self._store.append_event(
            run.id,
            "run.queued",
            "run.queued",
            {"run_id": run.id, "workflow": workflow.value},
        )
        self._schedule(run)
        return run

    async def resume_pending(self) -> None:
        for run in await self._store.list_resumable_runs():
            self._schedule(run)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _schedule(self, run: AgentRunRecord) -> None:
        task = asyncio.create_task(self._execute(run), name=f"agent-run-{run.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, run: AgentRunRecord) -> None:
        try:
            async with asyncio.timeout(run.budgets.timeout_seconds):
                await self._runtime.execute(run)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._store.mark_run(
                run.id, AgentRunStatus.FAILED, error_summary="Agent run timed out."
            )
            await self._store.append_event(
                run.id, "run.failed.timeout", "run.failed", {"reason": "timeout"}
            )
        except Exception as exc:
            await logger.aexception("agent_run_failed", run_id=run.id)
            await self._store.mark_run(
                run.id,
                AgentRunStatus.FAILED,
                error_summary=f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            await self._store.append_event(
                run.id,
                "run.failed.exception",
                "run.failed",
                {"reason": type(exc).__name__},
            )
