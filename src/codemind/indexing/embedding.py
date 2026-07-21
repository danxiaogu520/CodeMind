"""Deterministic local embedding baseline for offline development and tests."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+|\d+")


class HashEmbeddingProvider:
    """Feature-hashing embedding; replaceable through the EmbeddingProvider port."""

    model_id = "codemind-hash-embedding-v1"

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32:
            raise ValueError("Embedding dimensions must be at least 32.")
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in TOKEN_PATTERN.findall(text):
            normalized = token.lower().encode()
            digest = hashlib.blake2b(normalized, digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)
