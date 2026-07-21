"""Code-aware tokenization shared by sparse retrieval and query analysis."""

from __future__ import annotations

import re

WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+|[\u4e00-\u9fff]+")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize_code(text: str) -> list[str]:
    tokens: list[str] = []
    for match in WORD_PATTERN.findall(text):
        original = match.lower()
        tokens.append(original)
        for snake_part in match.split("_"):
            for camel_part in CAMEL_BOUNDARY.split(snake_part):
                normalized = camel_part.lower()
                if normalized and normalized != original:
                    tokens.append(normalized)
    return tokens
