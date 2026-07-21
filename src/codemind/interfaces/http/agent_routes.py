"""Durable Agent Run REST and SSE endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from codemind.application.agent_runs import AgentRunService
from codemind.application.ports import AgentRunStore
from codemind.domain.models import AgentRunRecord, AgentRunStatus, RunBudgets, WorkflowKind

router = APIRouter()
TERMINAL_STATUSES = {
    AgentRunStatus.COMPLETED,
    AgentRunStatus.PARTIAL,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}


class RunOptionsRequest(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=30)
    max_tool_calls: int = Field(default=8, ge=0, le=50)
    max_tokens: int = Field(default=12_000, ge=256, le=200_000)
    timeout_seconds: float = Field(default=120.0, ge=1, le=600)


class CreateRunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    workflow: WorkflowKind = WorkflowKind.AUTO
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    options: RunOptionsRequest = Field(default_factory=RunOptionsRequest)


class CreateRunResponse(BaseModel):
    id: str
    repository_id: str
    index_version_id: str
    workflow: str
    status: str


class CitationResponse(BaseModel):
    evidence_id: str
    path: str
    start_line: int
    end_line: int


class AnswerResponse(BaseModel):
    text: str
    citations: list[CitationResponse]
    incomplete: bool


class UsageResponse(BaseModel):
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class RunResponse(CreateRunResponse):
    session_id: str | None
    question: str
    answer: AnswerResponse | None
    usage: UsageResponse
    error_summary: str | None
    cancel_requested: bool


def _store(request: Request) -> AgentRunStore:
    return request.app.state.agent_run_store


def _service(request: Request) -> AgentRunService:
    return request.app.state.agent_run_service


@router.post(
    "/repositories/{repository_id}/runs",
    response_model=CreateRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["agent"],
)
async def create_run(
    repository_id: str, payload: CreateRunRequest, request: Request
) -> CreateRunResponse:
    budgets = RunBudgets(
        payload.options.max_steps,
        payload.options.max_tool_calls,
        payload.options.max_tokens,
        payload.options.timeout_seconds,
    )
    try:
        run = await _service(request).create_run(
            repository_id,
            payload.workflow,
            payload.question,
            payload.session_id,
            budgets,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Repository not found.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="Repository index is not ready.") from exc
    return CreateRunResponse(
        id=run.id,
        repository_id=run.repository_id,
        index_version_id=run.index_version_id,
        workflow=run.workflow.value,
        status=run.status.value,
    )


@router.get("/runs/{run_id}", response_model=RunResponse, tags=["agent"])
async def get_run(run_id: str, request: Request) -> RunResponse:
    run = await _store(request).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return _run_response(run)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse, tags=["agent"])
async def cancel_run(run_id: str, request: Request) -> RunResponse:
    run = await _store(request).request_cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    await _store(request).append_event(
        run_id, "run.cancel.requested", "run.cancel.requested", {"run_id": run_id}
    )
    return _run_response(run)


@router.get("/runs/{run_id}/events", tags=["agent"])
async def stream_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if await _store(request).get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    cursor = after
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))
    return StreamingResponse(
        _event_stream(request, run_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(request: Request, run_id: str, cursor: int) -> AsyncGenerator[str]:
    idle_cycles = 0
    while True:
        if await request.is_disconnected():
            return
        events = await _store(request).list_events(run_id, cursor)
        for event in events:
            cursor = event.sequence
            data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"
        run = await _store(request).get_run(run_id)
        if run is None or (run.status in TERMINAL_STATUSES and not events):
            return
        if not events:
            idle_cycles += 1
            if idle_cycles % 20 == 0:
                yield ": keep-alive\n\n"
        else:
            idle_cycles = 0
        await asyncio.sleep(0.25)


def _run_response(run: AgentRunRecord) -> RunResponse:
    answer = None
    if run.answer is not None:
        answer = AnswerResponse(
            text=run.answer.text,
            incomplete=run.answer.incomplete,
            citations=[
                CitationResponse(
                    evidence_id=item.evidence_id,
                    path=item.path,
                    start_line=item.start_line,
                    end_line=item.end_line,
                )
                for item in run.answer.citations
            ],
        )
    return RunResponse(
        id=run.id,
        repository_id=run.repository_id,
        index_version_id=run.index_version_id,
        session_id=run.session_id,
        workflow=run.workflow.value,
        question=run.question,
        status=run.status.value,
        answer=answer,
        usage=UsageResponse(
            steps=run.steps_used,
            tool_calls=run.tool_calls_used,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
        ),
        error_summary=run.error_summary,
        cancel_requested=run.cancel_requested,
    )
