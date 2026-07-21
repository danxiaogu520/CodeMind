from __future__ import annotations

import hashlib
from multiprocessing.connection import Connection
from typing import cast

import pytest

from codemind.domain.models import (
    Language,
    ParsedFile,
    RelationType,
    SourceDocument,
    SymbolKind,
)
from codemind.parsing.isolated_parser import IsolatedCodeParser, ParserProcessError
from codemind.parsing.tree_sitter_parser import TreeSitterCodeParser

SAMPLES = {
    Language.PYTHON: b"class Service:\n    def run(self):\n        helper()\n",
    Language.RUST: (
        b"trait Runner { fn run(&self); }\nimpl Runner for App { fn run(&self) { helper(); } }\n"
    ),
    Language.JAVASCRIPT: b"class Service { run() { helper(); } }\n",
    Language.TYPESCRIPT: b"interface Runner { run(): void }\nfunction helper(): void {}\n",
}


def document(language: Language, content: bytes) -> SourceDocument:
    return SourceDocument(
        path=f"src/example.{language.value}",
        language=language,
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.parametrize("language", list(Language))
def test_parser_extracts_symbols_for_all_supported_languages(language: Language) -> None:
    parsed = TreeSitterCodeParser().parse(document(language, SAMPLES[language]))

    assert parsed.symbols
    assert parsed.errors == ()
    assert all(symbol.start_line <= symbol.end_line for symbol in parsed.symbols)


def test_parser_extracts_qualified_method_and_call_relation() -> None:
    parsed = TreeSitterCodeParser().parse(document(Language.PYTHON, SAMPLES[Language.PYTHON]))

    method = next(symbol for symbol in parsed.symbols if symbol.name == "run")
    assert method.qualified_name == "Service.run"
    assert method.kind is SymbolKind.METHOD
    assert any(
        relation.type is RelationType.CALLS and relation.target_text == "helper"
        for relation in parsed.relations
    )


def test_parser_sanitizes_large_rust_tree_ranges() -> None:
    statements = "\n".join(
        f"    let value_{index} = source.lookup({index});" for index in range(350)
    )
    content = (
        "pub fn large(source: &Source) {\n"
        f"{statements}\n"
        "}\n"
        "fn tail(value: usize) -> usize { value }\n"
    ).encode()

    parsed = TreeSitterCodeParser().parse(document(Language.RUST, content))

    tail = next(symbol for symbol in parsed.symbols if symbol.name == "tail")
    assert tail.end_byte <= len(content)
    assert tail.start_line <= tail.end_line
    assert any(relation.type is RelationType.CALLS for relation in parsed.relations)


def parser_process_that_exits_on_bad_document(connection: Connection) -> None:
    request = cast(SourceDocument, connection.recv())
    if b"bad" in request.content:
        connection.close()
        return
    connection.send(("ok", ParsedFile(request, (), ())))
    if connection.recv() is None:
        connection.close()


def test_isolated_parser_restarts_after_child_process_exit() -> None:
    parser = IsolatedCodeParser(
        timeout_seconds=5,
        worker_target=parser_process_that_exits_on_bad_document,
    )
    try:
        with pytest.raises(ParserProcessError, match="crashed"):
            parser.parse(document(Language.RUST, b"fn bad() {}"))

        good = document(Language.RUST, b"fn good() {}")
        assert parser.parse(good).document == good
    finally:
        parser.close()
