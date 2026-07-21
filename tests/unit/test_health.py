from __future__ import annotations

from dataclasses import dataclass

from httpx import AsyncClient

from codemind.infrastructure.health import evaluate_readiness


@dataclass
class FakeCheck:
    name: str
    fails: bool = False

    async def check(self) -> None:
        if self.fails:
            raise ConnectionError


async def test_liveness_does_not_require_infrastructure(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_component_failure() -> None:
    ready, statuses = await evaluate_readiness(
        [FakeCheck("postgres"), FakeCheck("qdrant", fails=True)], 0.1
    )

    assert ready is False
    assert statuses == {"postgres": "ok", "qdrant": "unavailable"}


async def test_readiness_endpoint_returns_503(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
