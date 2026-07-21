"""Multi-language Tree-sitter parser producing CodeMind domain objects."""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

from codemind.domain.models import (
    Language,
    ParsedFile,
    ParsedRelation,
    ParsedSymbol,
    ParseStatus,
    RelationType,
    SourceDocument,
    SymbolKind,
)

SYMBOL_NODES: dict[Language, dict[str, SymbolKind]] = {
    Language.PYTHON: {
        "class_definition": SymbolKind.CLASS,
        "function_definition": SymbolKind.FUNCTION,
    },
    Language.RUST: {
        "function_item": SymbolKind.FUNCTION,
        "function_signature_item": SymbolKind.METHOD,
        "struct_item": SymbolKind.STRUCT,
        "enum_item": SymbolKind.ENUM,
        "trait_item": SymbolKind.TRAIT,
        "impl_item": SymbolKind.IMPL,
        "type_item": SymbolKind.TYPE_ALIAS,
        "const_item": SymbolKind.CONSTANT,
    },
    Language.JAVASCRIPT: {
        "function_declaration": SymbolKind.FUNCTION,
        "class_declaration": SymbolKind.CLASS,
        "method_definition": SymbolKind.METHOD,
    },
    Language.TYPESCRIPT: {
        "function_declaration": SymbolKind.FUNCTION,
        "class_declaration": SymbolKind.CLASS,
        "abstract_class_declaration": SymbolKind.CLASS,
        "method_definition": SymbolKind.METHOD,
        "method_signature": SymbolKind.METHOD,
        "interface_declaration": SymbolKind.INTERFACE,
        "type_alias_declaration": SymbolKind.TYPE_ALIAS,
        "enum_declaration": SymbolKind.ENUM,
    },
}

IMPORT_NODES: dict[Language, set[str]] = {
    Language.PYTHON: {"import_statement", "import_from_statement"},
    Language.RUST: {"use_declaration", "extern_crate_declaration"},
    Language.JAVASCRIPT: {"import_statement"},
    Language.TYPESCRIPT: {"import_statement"},
}

CALL_NODES: dict[Language, set[str]] = {
    Language.PYTHON: {"call"},
    Language.RUST: {"call_expression", "macro_invocation"},
    Language.JAVASCRIPT: {"call_expression", "new_expression"},
    Language.TYPESCRIPT: {"call_expression", "new_expression"},
}

CONTAINER_KINDS = {
    SymbolKind.CLASS,
    SymbolKind.TRAIT,
    SymbolKind.INTERFACE,
    SymbolKind.IMPL,
    SymbolKind.STRUCT,
    SymbolKind.ENUM,
}


@dataclass(slots=True)
class _WalkState:
    symbols: list[ParsedSymbol]
    relations: list[ParsedRelation]


class TreeSitterCodeParser:
    """Parse Python, Rust, JavaScript, and TypeScript into a shared schema."""

    def __init__(self) -> None:
        self._parsers: dict[Language, Parser] = {
            language: get_parser(language.value) for language in Language
        }

    def parse(self, document: SourceDocument) -> ParsedFile:
        parser = self._parsers[document.language]
        tree = parser.parse(document.content)
        state = _WalkState([], [])
        self._walk(document, tree.root_node, (), None, state)
        errors = ("tree_sitter_parse_error",) if tree.root_node.has_error else ()
        return ParsedFile(
            document=document,
            symbols=tuple(state.symbols),
            relations=tuple(state.relations),
            status=ParseStatus.PARTIAL if errors else ParseStatus.PARSED,
            errors=errors,
        )

    def _walk(
        self,
        document: SourceDocument,
        node: Node,
        parent_names: tuple[str, ...],
        current_symbol: ParsedSymbol | None,
        state: _WalkState,
    ) -> None:
        # With py-tree-sitter 0.26, retaining every wrapper or interleaving traversal with
        # child-field lookup can corrupt native Node state on larger Rust trees. Stream the
        # tree once and retain only symbol/relation nodes, then extract fields in two passes.
        # The explicit stack also avoids Python recursion limits.
        symbol_nodes: list[tuple[Node, int | None]] = []
        pending: list[tuple[Node, int | None]] = [(node, None)]
        while pending:
            current_node, owner_index = pending.pop()
            child_owner_index = owner_index
            if current_node.type in SYMBOL_NODES[document.language]:
                child_owner_index = len(symbol_nodes)
                symbol_nodes.append((current_node, owner_index))
            pending.extend(
                (child, child_owner_index) for child in reversed(current_node.named_children)
            )

        extracted_symbols: list[ParsedSymbol | None] = []
        for current_node, owner_index in symbol_nodes:
            owner = current_symbol if owner_index is None else extracted_symbols[owner_index]
            names = (
                (*parent_names, *owner.qualified_name.split("."))
                if owner is not None
                else parent_names
            )
            symbol = self._symbol_for_node(document, current_node, names, owner)
            if symbol is not None:
                state.symbols.append(symbol)
                if owner is not None:
                    state.relations.append(
                        ParsedRelation(
                            type=RelationType.CONTAINS,
                            source_local_id=owner.local_id,
                            target_text=symbol.qualified_name,
                            confidence=1.0,
                        )
                    )
            extracted_symbols.append(symbol)

        # Traverse again only after all retained symbol nodes have been released. Holding
        # both symbol and call-expression wrappers during child-field lookup triggers the
        # same binding lifetime defect.
        symbol_nodes.clear()
        relation_nodes: list[tuple[Node, int | None]] = []
        relation_tree = self._parsers[document.language].parse(document.content)
        pending = [(relation_tree.root_node, None)]
        symbol_index = 0
        while pending:
            current_node, owner_index = pending.pop()
            child_owner_index = owner_index
            if current_node.type in SYMBOL_NODES[document.language]:
                child_owner_index = symbol_index
                symbol_index += 1
            if (
                current_node.type in IMPORT_NODES[document.language]
                or current_node.type in CALL_NODES[document.language]
            ):
                relation_nodes.append((current_node, owner_index))
            pending.extend(
                (child, child_owner_index) for child in reversed(current_node.named_children)
            )

        for current_node, owner_index in relation_nodes:
            owner = current_symbol if owner_index is None else extracted_symbols[owner_index]
            if current_node.type in IMPORT_NODES[document.language]:
                state.relations.append(
                    ParsedRelation(
                        type=RelationType.IMPORTS,
                        source_local_id=owner.local_id if owner else None,
                        target_text=self._text(document, current_node).strip()[:500],
                        confidence=0.8,
                    )
                )
            if current_node.type in CALL_NODES[document.language]:
                target = current_node.child_by_field_name("function")
                if target is None:
                    target = current_node.child_by_field_name("macro")
                if target is not None:
                    state.relations.append(
                        ParsedRelation(
                            type=RelationType.CALLS,
                            source_local_id=owner.local_id if owner else None,
                            target_text=self._text(document, target).strip()[:300],
                            confidence=0.65,
                        )
                    )

    def _symbol_for_node(
        self,
        document: SourceDocument,
        node: Node,
        parent_names: tuple[str, ...],
        current_symbol: ParsedSymbol | None,
    ) -> ParsedSymbol | None:
        kind = SYMBOL_NODES[document.language].get(node.type)
        if kind is None:
            return None
        name_node = node.child_by_field_name("name")
        if node.type == "impl_item":
            name = self._impl_name(document, node)
        elif name_node is not None:
            name = self._text(document, name_node).strip()
        else:
            return None
        if not name:
            return None
        if (
            kind is SymbolKind.FUNCTION
            and current_symbol
            and current_symbol.kind in CONTAINER_KINDS
        ):
            kind = SymbolKind.METHOD
        qualified_name = ".".join((*parent_names, name)) if parent_names else name
        start_byte, end_byte = self._byte_range(document, node)
        start_line = document.content.count(b"\n", 0, start_byte) + 1
        end_line = start_line + document.content.count(b"\n", start_byte, end_byte)
        return ParsedSymbol(
            local_id=f"{qualified_name}:{start_byte}",
            qualified_name=qualified_name,
            name=name,
            kind=kind,
            signature=self._signature(document, node),
            start_line=start_line,
            end_line=max(start_line, end_line),
            start_byte=start_byte,
            end_byte=end_byte,
            parent_local_id=current_symbol.local_id if current_symbol else None,
        )

    def _signature(self, document: SourceDocument, node: Node) -> str:
        start_byte, end_byte = self._byte_range(document, node)
        body = node.child_by_field_name("body")
        if body is not None:
            body_start, _ = self._byte_range(document, body)
            if body_start >= start_byte:
                end_byte = min(end_byte, body_start)
        signature = document.content[start_byte:end_byte].decode("utf-8", errors="replace")
        signature = " ".join(signature.strip().split())
        return signature[:1000]

    def _impl_name(self, document: SourceDocument, node: Node) -> str:
        trait = node.child_by_field_name("trait")
        target_type = node.child_by_field_name("type")
        if trait is not None and target_type is not None:
            return f"impl {self._text(document, trait)} for {self._text(document, target_type)}"
        if target_type is not None:
            return f"impl {self._text(document, target_type)}"
        return "impl"

    @staticmethod
    def _text(document: SourceDocument, node: Node) -> str:
        start_byte, end_byte = TreeSitterCodeParser._byte_range(document, node)
        return document.content[start_byte:end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _byte_range(document: SourceDocument, node: Node) -> tuple[int, int]:
        content_length = len(document.content)
        start_byte = min(max(0, node.start_byte), content_length)
        end_byte = min(max(start_byte, node.end_byte), content_length)
        return start_byte, end_byte
