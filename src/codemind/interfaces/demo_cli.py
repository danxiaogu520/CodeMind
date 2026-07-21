"""Zero-dependency CLI for demonstrating the public CodeMind API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, NoReturn, cast
from urllib.parse import urlencode, urlparse

TERMINAL = {"completed", "partial", "failed", "cancelled"}


@dataclass(frozen=True, slots=True)
class DemoResult:
    repository_id: str
    job: dict[str, Any]
    run: dict[str, Any]
    memories: dict[str, Any]


class ApiClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        normalized = base_url.rstrip("/")
        if urlparse(normalized).scheme not in {"http", "https"}:
            raise ValueError("CodeMind API URL must use HTTP(S).")
        self.base_url = normalized
        self.timeout = timeout

    def request(self, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(  # noqa: S310 - scheme validated in constructor
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return cast(dict[str, Any], json.load(response))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach CodeMind API: {exc.reason}") from exc

    def import_repository(
        self, name: str, source_type: str, source: str, ref: str | None
    ) -> tuple[str, dict[str, Any]]:
        location = {"type": source_type}
        location["path" if source_type == "local" else "url"] = source
        payload: dict[str, object] = {"name": name, "source": location}
        if ref:
            payload["ref"] = ref
        created = self.request("/api/v1/repositories", payload)
        repository_id = str(created["repository_id"])
        job = self.wait_for_job(str(created["job_id"]))
        return repository_id, job

    def wait_for_job(self, job_id: str, *, timeout: float = 180.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_stage = ""
        while time.monotonic() < deadline:
            job = self.request(f"/api/v1/index-jobs/{job_id}")
            stage = str(job.get("stage", ""))
            if stage != last_stage:
                progress = cast(dict[str, Any], job.get("progress", {}))
                print(
                    f"  index: {stage} ({progress.get('processed', 0)}/{progress.get('total', 0)})"
                )
                last_stage = stage
            status = str(job.get("status"))
            if status in TERMINAL:
                if status not in {"completed", "partial"}:
                    raise RuntimeError(f"Index job ended with status={status}: {job}")
                return job
            time.sleep(0.25)
        raise TimeoutError("Timed out waiting for index job.")

    def ask(
        self,
        repository_id: str,
        question: str,
        workflow: str,
        session_id: str | None,
        *,
        stream: bool = True,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {"question": question, "workflow": workflow}
        if session_id:
            payload["session_id"] = session_id
        created = self.request(f"/api/v1/repositories/{repository_id}/runs", payload)
        run_id = str(created["id"])
        print(f"  run: {run_id}")
        if stream:
            self.stream_events(run_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.request(f"/api/v1/runs/{run_id}")
            if str(run.get("status")) in TERMINAL:
                return run
            time.sleep(0.2)
        raise TimeoutError("Timed out waiting for Agent run.")

    def stream_events(self, run_id: str) -> None:
        request = urllib.request.Request(  # noqa: S310 - scheme validated in constructor
            f"{self.base_url}/api/v1/runs/{run_id}/events",
            headers={"Accept": "text/event-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
                event_type = "message"
                for raw_line in response:
                    line = raw_line.decode(errors="replace").strip()
                    if line.startswith("event:"):
                        event_type = line.partition(":")[2].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line.partition(":")[2].strip())
                        if event_type in {
                            "step.started",
                            "step.completed",
                            "tool.completed",
                            "memory.recalled",
                            "memory.compacted",
                            "memory.persisted",
                            "run.completed",
                            "run.partial",
                            "run.failed",
                        }:
                            print(f"  event: {event_type} {json.dumps(data, ensure_ascii=False)}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"SSE endpoint returned HTTP {exc.code}.") from exc

    def memories(self, repository_id: str, query: str, session_id: str | None) -> dict[str, Any]:
        params = {"query": query}
        if session_id:
            params["session_id"] = session_id
        return self.request(f"/api/v1/repositories/{repository_id}/memories?{urlencode(params)}")


def run_demo(
    client: ApiClient,
    *,
    name: str,
    source_type: str,
    source: str,
    ref: str | None,
    question: str,
    workflow: str,
    session_id: str,
    stream: bool,
) -> DemoResult:
    print("[1/3] Importing repository")
    repository_id, job = client.import_repository(name, source_type, source, ref)
    print(f"  repository: {repository_id}")
    print(f"  delta: {json.dumps(job.get('delta', {}), ensure_ascii=False)}")
    print("[2/3] Running grounded Agent analysis")
    run = client.ask(repository_id, question, workflow, session_id, stream=stream)
    print("[3/3] Reading layered memory")
    memories = client.memories(repository_id, question, session_id)
    return DemoResult(repository_id, job, run, memories)


def print_answer(run: dict[str, Any]) -> None:
    answer = cast(dict[str, Any] | None, run.get("answer"))
    print(f"\nStatus: {run.get('status')}")
    if not answer:
        print(f"No answer. Error: {run.get('error_summary')}")
        return
    print("\nAnswer\n------")
    print(str(answer.get("text", "")))
    print("\nCitations\n---------")
    for citation in cast(list[dict[str, Any]], answer.get("citations", [])):
        print(
            f"- {citation.get('path')}:{citation.get('start_line')}-{citation.get('end_line')} "
            f"({citation.get('evidence_id')})"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodeMind product demo CLI")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check liveness and infrastructure readiness")

    importer = subparsers.add_parser("import", help="Import and index a repository")
    importer.add_argument("--name", required=True)
    importer.add_argument("--type", choices=("local", "git"), default="local")
    importer.add_argument("--source", required=True)
    importer.add_argument("--ref")

    asker = subparsers.add_parser("ask", help="Run an Agent workflow")
    asker.add_argument("--repository-id", required=True)
    asker.add_argument("--question", required=True)
    asker.add_argument(
        "--workflow",
        choices=("auto", "answer_question", "explain_module", "trace_symbol"),
        default="auto",
    )
    asker.add_argument("--session-id")
    asker.add_argument("--no-stream", action="store_true")

    memory = subparsers.add_parser("memories", help="Inspect current project memory")
    memory.add_argument("--repository-id", required=True)
    memory.add_argument("--query", default="")
    memory.add_argument("--session-id")

    demo = subparsers.add_parser("demo", help="Run import → Agent → memory end to end")
    demo.add_argument("--name", default="codemind-sample")
    demo.add_argument("--type", choices=("local", "git"), default="local")
    demo.add_argument("--source", default="/fixtures/sample_python")
    demo.add_argument("--ref")
    demo.add_argument("--question", default="Trace calls to create_token")
    demo.add_argument("--workflow", default="trace_symbol")
    demo.add_argument("--session-id", default="three-minute-demo")
    demo.add_argument("--no-stream", action="store_true")
    return parser


def _fail(message: str) -> NoReturn:
    print(f"CodeMind demo failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    args = build_parser().parse_args()
    try:
        client = ApiClient(str(args.base_url))
        if args.command == "doctor":
            print(json.dumps(client.request("/health/live"), ensure_ascii=False, indent=2))
            print(json.dumps(client.request("/health/ready"), ensure_ascii=False, indent=2))
        elif args.command == "import":
            repository_id, job = client.import_repository(
                str(args.name), str(args.type), str(args.source), args.ref
            )
            print(json.dumps({"repository_id": repository_id, "job": job}, indent=2))
        elif args.command == "ask":
            run = client.ask(
                str(args.repository_id),
                str(args.question),
                str(args.workflow),
                args.session_id,
                stream=not bool(args.no_stream),
            )
            print_answer(run)
        elif args.command == "memories":
            result = client.memories(str(args.repository_id), str(args.query), args.session_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "demo":
            result = run_demo(
                client,
                name=str(args.name),
                source_type=str(args.type),
                source=str(args.source),
                ref=args.ref,
                question=str(args.question),
                workflow=str(args.workflow),
                session_id=str(args.session_id),
                stream=not bool(args.no_stream),
            )
            print_answer(result.run)
            items = cast(list[dict[str, Any]], result.memories.get("items", []))
            layers = sorted({str(item.get("layer")) for item in items})
            print(f"\nMemory layers: {', '.join(layers) or 'none'}")
            print(f"Repository ID: {result.repository_id}")
    except (RuntimeError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _fail(str(exc))
