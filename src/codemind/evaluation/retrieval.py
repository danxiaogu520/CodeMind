"""JSONL retrieval benchmark runner for the public Search API."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class RelevantItem:
    path: str
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    question: str
    relevant: tuple[RelevantItem, ...]
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseResult:
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank: float
    ndcg_at_10: float
    latency_ms: float


def load_cases(path: Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            cases.append(
                BenchmarkCase(
                    question=str(data["question"]),
                    relevant=tuple(
                        RelevantItem(path=str(item["path"]), symbol=item.get("symbol"))
                        for item in data["relevant"]
                    ),
                    tags=tuple(str(tag) for tag in data.get("tags", [])),
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid benchmark case at line {line_number}.") from exc
    if not cases:
        raise ValueError("Benchmark dataset is empty.")
    return cases


def score_case(
    case: BenchmarkCase, evidence: list[dict[str, Any]], latency_ms: float
) -> CaseResult:
    relevant = set(case.relevant)
    matched: set[RelevantItem] = set()
    binary_relevance: list[int] = []
    for item in evidence:
        candidate = RelevantItem(path=str(item.get("path", "")), symbol=item.get("symbol"))
        match = next(
            (
                expected
                for expected in relevant
                if expected.path == candidate.path
                and (expected.symbol is None or expected.symbol == candidate.symbol)
            ),
            None,
        )
        binary_relevance.append(1 if match is not None and match not in matched else 0)
        if match is not None:
            matched.add(match)
    denominator = max(1, len(relevant))
    recall_5 = sum(binary_relevance[:5]) / denominator
    recall_10 = sum(binary_relevance[:10]) / denominator
    first_rank = next((index for index, value in enumerate(binary_relevance, 1) if value), None)
    reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
    dcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(binary_relevance[:10], 1))
    ideal_count = min(len(relevant), 10)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return CaseResult(recall_5, recall_10, reciprocal_rank, dcg / ideal, latency_ms)


def summarize(results: list[CaseResult]) -> dict[str, float | int]:
    if not results:
        raise ValueError("No benchmark results were supplied.")
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
    return {
        "cases": len(results),
        "recall_at_5": statistics.fmean(result.recall_at_5 for result in results),
        "recall_at_10": statistics.fmean(result.recall_at_10 for result in results),
        "mrr": statistics.fmean(result.reciprocal_rank for result in results),
        "ndcg_at_10": statistics.fmean(result.ndcg_at_10 for result in results),
        "latency_avg_ms": statistics.fmean(result.latency_ms for result in results),
        "latency_p95_ms": latencies[p95_index],
    }


def query_api(
    base_url: str, repository_id: str, question: str
) -> tuple[list[dict[str, Any]], float]:
    if urlparse(base_url).scheme not in {"http", "https"}:
        raise ValueError("Benchmark base URL must use HTTP(S).")
    request = urllib.request.Request(  # noqa: S310 - scheme validated above
        f"{base_url.rstrip('/')}/api/v1/repositories/{repository_id}/search",
        data=json.dumps({"query": question, "limit": 10}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Search API returned HTTP {exc.code}: {detail}") from exc
    latency_ms = (time.perf_counter() - started) * 1000
    return list(payload["evidence"]), latency_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeMind retrieval benchmark.")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-recall-at-10", type=float, default=0.8)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    results = [
        score_case(case, *query_api(args.base_url, args.repository_id, case.question))
        for case in cases
    ]
    report = summarize(results)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if float(report["recall_at_10"]) < args.min_recall_at_10:
        sys.exit(1)
