"""End-to-end benchmark runner for durable Agent workflows."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class AgentCase:
    workflow: str
    question: str
    expected_paths: tuple[str, ...]


def load_cases(path: Path) -> list[AgentCase]:
    cases: list[AgentCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            cases.append(
                AgentCase(
                    workflow=str(value["workflow"]),
                    question=str(value["question"]),
                    expected_paths=tuple(str(item) for item in value["expected_paths"]),
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Agent benchmark case at line {line_number}.") from exc
    if not cases:
        raise ValueError("Agent benchmark dataset is empty.")
    return cases


def run_case(base_url: str, repository_id: str, case: AgentCase) -> dict[str, object]:
    create = _request(
        f"{base_url}/api/v1/repositories/{repository_id}/runs",
        {"workflow": case.workflow, "question": case.question},
    )
    run_id = str(create["id"])
    started = time.perf_counter()
    result: dict[str, Any] = {}
    for _ in range(600):
        result = _request(f"{base_url}/api/v1/runs/{run_id}")
        if result["status"] in {"completed", "partial", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    latency_ms = (time.perf_counter() - started) * 1000
    answer = cast(dict[str, Any] | None, result.get("answer"))
    citations = cast(list[dict[str, Any]], answer.get("citations", []) if answer else [])
    paths = {str(item.get("path", "")) for item in citations}
    valid = all(
        str(item.get("evidence_id", "")).startswith("ev_")
        and int(item.get("start_line", 0)) > 0
        and int(item.get("end_line", 0)) >= int(item.get("start_line", 0))
        for item in citations
    )
    expected = set(case.expected_paths)
    return {
        "completed": result.get("status") == "completed",
        "citation_valid": bool(citations) and valid,
        "expected_path_recall": len(paths & expected) / max(1, len(expected)),
        "latency_ms": latency_ms,
    }


def summarize(results: list[dict[str, object]]) -> dict[str, float | int]:
    if not results:
        raise ValueError("No Agent benchmark results were supplied.")
    return {
        "cases": len(results),
        "task_completion_rate": statistics.fmean(
            1.0 if result["completed"] else 0.0 for result in results
        ),
        "citation_validity": statistics.fmean(
            1.0 if result["citation_valid"] else 0.0 for result in results
        ),
        "expected_path_recall": statistics.fmean(
            cast(float, result["expected_path_recall"]) for result in results
        ),
        "latency_avg_ms": statistics.fmean(cast(float, result["latency_ms"]) for result in results),
    }


def _request(url: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("Agent benchmark URL must use HTTP(S).")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - scheme validated above
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return cast(dict[str, Any], json.load(response))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Agent API returned HTTP {exc.code}: {exc.read().decode()}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeMind Agent benchmark.")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-completion-rate", type=float, default=0.8)
    args = parser.parse_args()
    base_url = str(args.base_url).rstrip("/")
    results = [
        run_case(base_url, str(args.repository_id), case) for case in load_cases(args.dataset)
    ]
    report = summarize(results)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if float(report["task_completion_rate"]) < args.min_completion_rate:
        sys.exit(1)
