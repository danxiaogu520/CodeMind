"""Deterministic grounded answer provider and citation validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from codemind.domain.models import (
    AgentAnswer,
    AgentCitation,
    Evidence,
    WorkflowKind,
)


class TemplateGroundedLLM:
    """Offline baseline behind the LLMProvider port; never invents code facts."""

    model_id = "codemind-grounded-template-v1"

    async def generate_grounded_answer(
        self,
        workflow: WorkflowKind,
        question: str,
        evidence: Sequence[Evidence],
        observations: Sequence[dict[str, object]],
    ) -> AgentAnswer:
        del question
        if not evidence:
            return AgentAnswer(
                text="当前索引中没有找到足够证据, 无法给出可靠结论。",
                citations=(),
                incomplete=True,
            )
        citations = tuple(
            AgentCitation(item.id, item.path, item.start_line, item.end_line)
            for item in evidence[:5]
        )
        lines = [self._heading(workflow)]
        for item in evidence[:5]:
            subject = item.symbol or item.path
            lines.append(
                f"- `{subject}` 位于 `{item.path}:{item.start_line}-{item.end_line}` [{item.id}]。"
            )
        if workflow is WorkflowKind.TRACE_SYMBOL:
            relations = self._relations(observations)
            if relations:
                lines.append("静态关系:")
                for relation in relations[:10]:
                    lines.append(
                        f"- `{relation.get('source') or '<module>'}` "
                        f"--{relation.get('type')}--> `{relation.get('target')}` "
                        f"(confidence={relation.get('confidence')})"
                    )
            else:
                lines.append("未解析到可靠的静态调用边; 当前结论仅覆盖定义与相关源码。")
        elif workflow is WorkflowKind.EXPLAIN_MODULE:
            summary = next(
                (
                    observation
                    for observation in observations
                    if "languages" in observation and "symbol_kinds" in observation
                ),
                None,
            )
            if summary:
                lines.append(f"语言文件统计: {summary['languages']}。")
                lines.append(f"符号类型统计: {summary['symbol_kinds']}。")
        if observations:
            lines.append(f"工作流还检查了 {len(observations)} 组结构化项目事实。")
        lines.append("以上结论仅基于列出的代码证据。")
        return AgentAnswer(text="\n".join(lines), citations=citations)

    @staticmethod
    def _relations(observations: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        for observation in observations:
            value = observation.get("relations")
            if isinstance(value, list):
                return cast(list[dict[str, object]], value)
        return []

    @staticmethod
    def _heading(workflow: WorkflowKind) -> str:
        return {
            WorkflowKind.ANSWER_QUESTION: "代码检索结论:",
            WorkflowKind.EXPLAIN_MODULE: "模块职责与核心入口:",
            WorkflowKind.TRACE_SYMBOL: "符号定义与静态调用关系:",
            WorkflowKind.AUTO: "代码分析结论:",
        }[workflow]


def validate_citations(answer: AgentAnswer, evidence: Sequence[Evidence]) -> AgentAnswer:
    available = {item.id: item for item in evidence}
    valid = tuple(
        AgentCitation(
            citation.evidence_id,
            available[citation.evidence_id].path,
            available[citation.evidence_id].start_line,
            available[citation.evidence_id].end_line,
        )
        for citation in answer.citations
        if citation.evidence_id in available
    )
    incomplete = answer.incomplete or (bool(evidence) and not valid)
    if evidence and not valid:
        first = evidence[0]
        valid = (AgentCitation(first.id, first.path, first.start_line, first.end_line),)
    return AgentAnswer(answer.text, valid, incomplete)
