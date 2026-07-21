"""LangGraph adapter for bounded, durable CodeMind workflows."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from codemind.agent.grounding import validate_citations
from codemind.application.memory import compact_working_memory
from codemind.application.ports import AgentRunStore, LLMProvider, MemoryPort, WorkflowRuntime
from codemind.domain.models import (
    AgentAnswer,
    AgentCitation,
    AgentRunRecord,
    AgentRunStatus,
    Evidence,
    Language,
    RunBudgets,
    ToolContext,
    WorkflowKind,
)
from codemind.infrastructure.agent.tools import ReadOnlyCodeToolRegistry


class GraphState(TypedDict):
    run_id: str
    repository_id: str
    index_version_id: str
    session_id: str | None
    question: str
    workflow: str
    max_steps: int
    max_tool_calls: int
    max_tokens: int
    step_count: int
    tool_calls: int
    status: str
    plan: list[str]
    evidence: list[dict[str, object]]
    observations: list[dict[str, object]]
    answer: NotRequired[dict[str, object] | None]
    error: NotRequired[str | None]
    working_summary: NotRequired[dict[str, object] | None]


class LangGraphWorkflowRuntime(WorkflowRuntime):
    def __init__(
        self,
        *,
        store: AgentRunStore,
        tools: ReadOnlyCodeToolRegistry,
        llm: LLMProvider,
        checkpointer: object,
        memory: MemoryPort | None = None,
        node_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._tools = tools
        self._llm = llm
        self._memory = memory
        self._node_delay_seconds = node_delay_seconds
        builder = StateGraph(GraphState)
        builder.add_node("plan", self._plan)
        builder.add_node("retrieve", self._retrieve)
        builder.add_node("inspect", self._inspect)
        builder.add_node("synthesize", self._synthesize)
        builder.add_node("validate", self._validate)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "plan")
        self._conditional(builder, "plan", "retrieve")
        self._conditional(builder, "retrieve", "inspect")
        self._conditional(builder, "inspect", "synthesize")
        self._conditional(builder, "synthesize", "validate")
        builder.add_edge("validate", "finalize")
        builder.add_edge("finalize", END)
        self._graph = builder.compile(checkpointer=checkpointer)  # type: ignore[arg-type]

    @staticmethod
    def _conditional(builder: StateGraph[GraphState], source: str, target: str) -> None:
        builder.add_conditional_edges(
            source,
            LangGraphWorkflowRuntime._route,
            {"continue": target, "finalize": "finalize"},
        )

    @staticmethod
    def _route(state: GraphState) -> str:
        return "finalize" if state["status"] in {"cancelled", "failed", "partial"} else "continue"

    async def execute(self, run: AgentRunRecord) -> None:
        config = {"configurable": {"thread_id": run.id}}
        snapshot = await self._graph.aget_state(config)  # type: ignore[arg-type]
        inputs: GraphState | None = None if snapshot.values else self._initial_state(run)
        await self._graph.ainvoke(inputs, config=config)  # type: ignore[arg-type]

    @staticmethod
    def _initial_state(run: AgentRunRecord) -> GraphState:
        return GraphState(
            run_id=run.id,
            repository_id=run.repository_id,
            index_version_id=run.index_version_id,
            session_id=run.session_id,
            question=run.question,
            workflow=run.workflow.value,
            max_steps=run.budgets.max_steps,
            max_tool_calls=run.budgets.max_tool_calls,
            max_tokens=run.budgets.max_tokens,
            step_count=0,
            tool_calls=0,
            status="running",
            plan=[],
            evidence=[],
            observations=[],
            answer=None,
            error=None,
            working_summary=None,
        )

    async def _plan(self, state: GraphState) -> dict[str, object]:
        guarded = await self._begin(state, "plan", 1)
        if guarded is not None:
            return guarded
        workflow = self._resolve_workflow(state["workflow"], state["question"])
        observations = list(state["observations"])
        if self._memory is not None:
            memories = await self._memory.recall(
                state["repository_id"],
                state["index_version_id"],
                state["question"],
                state["session_id"],
            )
            observations.extend(
                {
                    "memory_layer": memory.layer.value,
                    "scope": memory.scope_key,
                    "content": memory.content,
                    "source_paths": list(memory.source_paths),
                    "untrusted_memory": True,
                }
                for memory in memories
            )
            await self._store.append_event(
                state["run_id"],
                "memory.recalled",
                "memory.recalled",
                {"count": len(memories), "layers": sorted({item.layer.value for item in memories})},
            )
        plan = {
            WorkflowKind.ANSWER_QUESTION: ["search_code", "read_symbol", "synthesize"],
            WorkflowKind.EXPLAIN_MODULE: [
                "search_code",
                "list_tree",
                "get_project_summary",
                "get_file_outline",
                "synthesize",
            ],
            WorkflowKind.TRACE_SYMBOL: [
                "search_code",
                "find_references",
                "read_symbol",
                "synthesize",
            ],
            WorkflowKind.AUTO: ["search_code", "synthesize"],
        }[workflow]
        await self._complete(state, "plan", 1, f"workflow={workflow.value}")
        return {
            "workflow": workflow.value,
            "plan": plan,
            "observations": observations,
            "step_count": state["step_count"] + 1,
        }

    async def _retrieve(self, state: GraphState) -> dict[str, object]:
        guarded = await self._begin(state, "retrieve", 2)
        if guarded is not None:
            return guarded
        if state["tool_calls"] >= state["max_tool_calls"]:
            return await self._budget_exhausted(state, "retrieve", 2)
        result = await self._tools.invoke(
            "search_code",
            {"query": state["question"], "limit": 12},
            self._context(state),
        )
        await self._tool_event(state, "search_code", len(result.evidence))
        await self._complete(state, "retrieve", 2, f"evidence={len(result.evidence)}")
        return {
            "evidence": [self._evidence_json(item) for item in result.evidence],
            "observations": [*state["observations"], result.data],
            "tool_calls": state["tool_calls"] + 1,
            "step_count": state["step_count"] + 1,
        }

    async def _inspect(self, state: GraphState) -> dict[str, object]:
        guarded = await self._begin(state, "inspect", 3)
        if guarded is not None:
            return guarded
        workflow = WorkflowKind(state["workflow"])
        calls: list[tuple[str, dict[str, object]]] = []
        evidence = [self._evidence(item) for item in state["evidence"]]
        first = evidence[0] if evidence else None
        if workflow is WorkflowKind.EXPLAIN_MODULE:
            calls.extend(
                [
                    ("list_tree", {"path": self._module_prefix(first), "depth": 3}),
                    ("get_project_summary", {}),
                ]
            )
            if first is not None:
                calls.append(("get_file_outline", {"path": first.path}))
        elif workflow is WorkflowKind.TRACE_SYMBOL:
            symbol = (
                first.symbol if first and first.symbol else self._symbol_hint(state["question"])
            )
            if symbol:
                calls.extend(
                    [
                        ("find_references", {"qualified_name": symbol}),
                        ("read_symbol", {"qualified_name": symbol}),
                    ]
                )
        elif first is not None and first.symbol:
            calls.append(("read_symbol", {"qualified_name": first.symbol}))

        observations = list(state["observations"])
        used = state["tool_calls"]
        for name, arguments in calls:
            if used >= state["max_tool_calls"]:
                break
            result = await self._tools.invoke(name, arguments, self._context(state))
            observations.append({"tool": name, **result.data})
            used += 1
            await self._tool_event(state, name, len(result.evidence))
        observations, working_summary = compact_working_memory(
            observations,
            [str(item["id"]) for item in state["evidence"]],
        )
        if working_summary is not None:
            await self._store.append_event(
                state["run_id"],
                "memory.working.compacted",
                "memory.compacted",
                working_summary,
            )
        await self._complete(state, "inspect", 3, f"tool_calls={used - state['tool_calls']}")
        return {
            "observations": observations,
            "tool_calls": used,
            "step_count": state["step_count"] + 1,
            "working_summary": working_summary,
        }

    async def _synthesize(self, state: GraphState) -> dict[str, object]:
        guarded = await self._begin(state, "synthesize", 4)
        if guarded is not None:
            return guarded
        evidence = [self._evidence(item) for item in state["evidence"]]
        answer = await self._llm.generate_grounded_answer(
            WorkflowKind(state["workflow"]),
            state["question"],
            evidence,
            state["observations"],
        )
        await self._store.append_event(
            state["run_id"],
            "answer.delta",
            "answer.delta",
            {"text": answer.text},
        )
        await self._complete(state, "synthesize", 4, f"citations={len(answer.citations)}")
        return {
            "answer": self._answer_json(answer),
            "step_count": state["step_count"] + 1,
        }

    async def _validate(self, state: GraphState) -> dict[str, object]:
        guarded = await self._begin(state, "validate", 5)
        if guarded is not None:
            return guarded
        evidence = [self._evidence(item) for item in state["evidence"]]
        answer = validate_citations(self._answer(state.get("answer")), evidence)
        await self._complete(state, "validate", 5, f"valid_citations={len(answer.citations)}")
        return {
            "answer": self._answer_json(answer),
            "status": "partial" if answer.incomplete else "completed",
            "step_count": state["step_count"] + 1,
        }

    async def _finalize(self, state: GraphState) -> dict[str, object]:
        status = AgentRunStatus(state["status"])
        answer_value = state.get("answer")
        answer = self._answer(answer_value) if answer_value else None
        input_tokens = max(
            1,
            (len(state["question"]) + sum(len(str(item)) for item in state["evidence"])) // 4,
        )
        output_tokens = max(0, len(answer.text) // 4) if answer else 0
        if (
            input_tokens + output_tokens > state["max_tokens"]
            and status is AgentRunStatus.COMPLETED
        ):
            status = AgentRunStatus.PARTIAL
            if answer is not None:
                answer = AgentAnswer(answer.text, answer.citations, incomplete=True)
        if (
            self._memory is not None
            and answer is not None
            and status
            in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.PARTIAL,
            }
        ):
            evidence = [self._evidence(item) for item in state["evidence"]]
            memory_run = AgentRunRecord(
                id=state["run_id"],
                repository_id=state["repository_id"],
                index_version_id=state["index_version_id"],
                session_id=state["session_id"],
                workflow=WorkflowKind(state["workflow"]),
                question=state["question"],
                status=status,
                budgets=RunBudgets(
                    state["max_steps"],
                    state["max_tool_calls"],
                    state["max_tokens"],
                ),
                steps_used=state["step_count"],
                tool_calls_used=state["tool_calls"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                answer=answer,
            )
            await self._memory.record_run(memory_run, answer, evidence)
            await self._store.append_event(
                state["run_id"],
                "memory.persisted",
                "memory.persisted",
                {"session": bool(state["session_id"]), "sources": len(evidence)},
            )
        await self._store.mark_run(
            state["run_id"],
            status,
            answer=answer,
            error_summary=state.get("error"),
            steps_used=state["step_count"],
            tool_calls_used=state["tool_calls"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        await self._store.append_event(
            state["run_id"],
            f"run.{status.value}",
            f"run.{status.value}",
            {"status": status.value, "steps": state["step_count"]},
        )
        return {"status": status.value}

    async def _begin(self, state: GraphState, node: str, sequence: int) -> dict[str, object] | None:
        if await self._store.is_cancel_requested(state["run_id"]):
            await self._store.record_step(
                state["run_id"], node, sequence, "cancelled", output_summary="cancel requested"
            )
            return {"status": "cancelled"}
        if state["step_count"] >= state["max_steps"]:
            return await self._budget_exhausted(state, node, sequence)
        await self._store.mark_run(state["run_id"], AgentRunStatus.RUNNING)
        await self._store.record_step(
            state["run_id"], node, sequence, "running", input_summary="state accepted"
        )
        await self._store.append_event(
            state["run_id"], f"step.{node}.started", "step.started", {"node": node}
        )
        if node == "plan":
            await self._store.append_event(
                state["run_id"], "run.started", "run.started", {"run_id": state["run_id"]}
            )
        return None

    async def _complete(self, state: GraphState, node: str, sequence: int, summary: str) -> None:
        await self._store.record_step(
            state["run_id"], node, sequence, "completed", output_summary=summary
        )
        await self._store.append_event(
            state["run_id"],
            f"step.{node}.completed",
            "step.completed",
            {"node": node, "summary": summary},
        )
        if self._node_delay_seconds:
            await asyncio.sleep(self._node_delay_seconds)

    async def _budget_exhausted(
        self, state: GraphState, node: str, sequence: int
    ) -> dict[str, object]:
        await self._store.record_step(
            state["run_id"], node, sequence, "partial", output_summary="budget exhausted"
        )
        await self._store.append_event(
            state["run_id"],
            f"step.{node}.budget",
            "budget.exhausted",
            {"node": node},
        )
        return {"status": "partial", "error": "Run budget exhausted."}

    async def _tool_event(self, state: GraphState, name: str, evidence_count: int) -> None:
        occurrence = state["tool_calls"] + 1
        await self._store.append_event(
            state["run_id"],
            f"tool.{occurrence}.{name}",
            "tool.completed",
            {"tool": name, "evidence_count": evidence_count},
        )

    @staticmethod
    def _resolve_workflow(value: str, question: str) -> WorkflowKind:
        workflow = WorkflowKind(value)
        if workflow is not WorkflowKind.AUTO:
            return workflow
        lowered = question.lower()
        if any(cue in lowered for cue in ("调用", "call", "trace", "引用")):
            return WorkflowKind.TRACE_SYMBOL
        if any(cue in lowered for cue in ("模块", "module", "目录", "package")):
            return WorkflowKind.EXPLAIN_MODULE
        return WorkflowKind.ANSWER_QUESTION

    @staticmethod
    def _context(state: GraphState) -> ToolContext:
        return ToolContext(state["run_id"], state["repository_id"], state["index_version_id"])

    @staticmethod
    def _module_prefix(evidence: Evidence | None) -> str:
        if evidence is None or "/" not in evidence.path:
            return "src"
        return evidence.path.rsplit("/", 1)[0]

    @staticmethod
    def _symbol_hint(question: str) -> str:
        for token in question.replace("`", " ").split():
            stripped = token.strip(".,:;!?()[]{}")
            if "." in stripped or "_" in stripped:
                return stripped
        return ""

    @staticmethod
    def _evidence_json(item: Evidence) -> dict[str, object]:
        return {
            "id": item.id,
            "repository_id": item.repository_id,
            "index_version_id": item.index_version_id,
            "commit_sha": item.commit_sha,
            "path": item.path,
            "language": item.language.value if item.language else None,
            "symbol": item.symbol,
            "start_line": item.start_line,
            "end_line": item.end_line,
            "content": item.content,
            "rrf_score": item.rrf_score,
            "rerank_score": item.rerank_score,
            "reasons": list(item.reasons),
        }

    @staticmethod
    def _evidence(value: Mapping[str, object]) -> Evidence:
        language_value = value.get("language")
        return Evidence(
            id=str(value["id"]),
            repository_id=str(value["repository_id"]),
            index_version_id=str(value["index_version_id"]),
            commit_sha=str(value["commit_sha"]),
            path=str(value["path"]),
            language=Language(str(language_value)) if language_value else None,
            symbol=str(value["symbol"]) if value.get("symbol") else None,
            start_line=int(cast(int, value["start_line"])),
            end_line=int(cast(int, value["end_line"])),
            content=str(value["content"]),
            rrf_score=float(cast(float, value["rrf_score"])),
            rerank_score=(
                float(cast(float, value["rerank_score"]))
                if value.get("rerank_score") is not None
                else None
            ),
            reasons=tuple(str(reason) for reason in cast(Sequence[object], value["reasons"])),
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
    def _answer(value: Mapping[str, object] | None) -> AgentAnswer:
        if value is None:
            return AgentAnswer("", (), incomplete=True)
        citations = cast(Sequence[Mapping[str, object]], value.get("citations", []))
        return AgentAnswer(
            text=str(value.get("text", "")),
            incomplete=bool(value.get("incomplete", False)),
            citations=tuple(
                AgentCitation(
                    str(item["evidence_id"]),
                    str(item["path"]),
                    int(cast(int, item["start_line"])),
                    int(cast(int, item["end_line"])),
                )
                for item in citations
            ),
        )
