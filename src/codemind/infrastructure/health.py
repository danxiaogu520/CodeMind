"""Infrastructure readiness probes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, cast

from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codemind.infrastructure.models.http import get_json


class ReadinessCheck(Protocol):
    """A named check used to decide whether this process can serve traffic."""

    @property
    def name(self) -> str: ...

    async def check(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DatabaseReadinessCheck:
    engine: AsyncEngine
    name: str = "postgres"

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


@dataclass(frozen=True, slots=True)
class QdrantReadinessCheck:
    client: AsyncQdrantClient
    name: str = "qdrant"

    async def check(self) -> None:
        await self.client.get_collections()


@dataclass(frozen=True, slots=True)
class OllamaReadinessCheck:
    base_url: str
    request_timeout_seconds: float
    name: str = "ollama"

    async def check(self) -> None:
        response = await get_json(
            f"{self.base_url.rstrip('/')}/api/tags", self.request_timeout_seconds
        )
        if not isinstance(response, dict):
            raise RuntimeError("Ollama returned an invalid readiness response.")
        if not isinstance(cast(dict[str, object], response).get("models"), list):
            raise RuntimeError("Ollama returned an invalid readiness response.")


async def evaluate_readiness(
    checks: list[ReadinessCheck], timeout_seconds: float
) -> tuple[bool, dict[str, str]]:
    """Run probes concurrently and return sanitized component statuses."""

    async def run(check: ReadinessCheck) -> tuple[str, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                await check.check()
        except (TimeoutError, OSError, RuntimeError, ConnectionError):
            return check.name, "unavailable"
        # Third-party clients expose several transport-specific exception types.
        except Exception:
            return check.name, "unavailable"
        return check.name, "ok"

    results = dict(await asyncio.gather(*(run(check) for check in checks)))
    return all(status == "ok" for status in results.values()), results
