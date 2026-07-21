from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from codemind.config import Settings
from codemind.interfaces.http.app import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        log_format="console",
        database_url="postgresql+asyncpg://user:pass@127.0.0.1:1/test",
        qdrant_url="http://127.0.0.1:1",
        readiness_timeout_seconds=0.1,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client
