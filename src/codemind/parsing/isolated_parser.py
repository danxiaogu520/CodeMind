"""Crash-isolated Tree-sitter parser process."""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from contextlib import suppress
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import cast

from codemind.domain.models import ParsedFile, SourceDocument
from codemind.parsing.tree_sitter_parser import TreeSitterCodeParser

ParserWorker = Callable[[Connection], None]


class ParserProcessError(RuntimeError):
    """Raised when the native parser subprocess crashes, times out, or rejects a file."""


def run_parser_process(connection: Connection) -> None:
    """Serve parser requests until the parent closes the channel."""

    parser = TreeSitterCodeParser()
    try:
        while True:
            request = connection.recv()
            if request is None:
                return
            document = cast(SourceDocument, request)
            try:
                connection.send(("ok", parser.parse(document)))
            except Exception as exc:
                connection.send(("error", f"{type(exc).__name__}: {str(exc)[:400]}"))
    except (EOFError, BrokenPipeError):
        return
    finally:
        connection.close()


class IsolatedCodeParser:
    """Run native parsing behind a restartable process boundary."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        worker_target: ParserWorker = run_parser_process,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Parser timeout must be positive.")
        self._timeout_seconds = timeout_seconds
        self._worker_target = worker_target
        self._context = multiprocessing.get_context("spawn")
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None

    def parse(self, document: SourceDocument) -> ParsedFile:
        self._ensure_process()
        process = self._require_process()
        connection = self._require_connection()
        try:
            connection.send(document)
            if not connection.poll(self._timeout_seconds):
                raise ParserProcessError(
                    f"Parser timed out after {self._timeout_seconds:g}s for {document.path}."
                )
            status, payload = connection.recv()
        except (BrokenPipeError, EOFError, OSError) as exc:
            exit_code = process.exitcode
            self._reset_process()
            raise ParserProcessError(
                f"Parser process crashed for {document.path} (exit code {exit_code})."
            ) from exc
        except ParserProcessError:
            self._reset_process()
            raise

        if not process.is_alive():
            self._reset_process()

        if status == "ok":
            return cast(ParsedFile, payload)
        raise ParserProcessError(f"Parser failed for {document.path}: {payload}")

    def close(self) -> None:
        process = self._process
        connection = self._connection
        if process is None:
            return
        if process.is_alive() and connection is not None:
            with suppress(BrokenPipeError, EOFError, OSError):
                connection.send(None)
            process.join(timeout=2)
        self._reset_process()

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._reset_process()
        parent_connection, child_connection = self._context.Pipe()
        process = self._context.Process(
            target=self._worker_target,
            args=(child_connection,),
            name="codemind-parser",
            daemon=True,
        )
        process.start()
        child_connection.close()
        self._process = process
        self._connection = parent_connection

    def _reset_process(self) -> None:
        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()

    def _require_process(self) -> BaseProcess:
        if self._process is None:
            raise ParserProcessError("Parser process is unavailable.")
        return self._process

    def _require_connection(self) -> Connection:
        if self._connection is None:
            raise ParserProcessError("Parser connection is unavailable.")
        return self._connection
