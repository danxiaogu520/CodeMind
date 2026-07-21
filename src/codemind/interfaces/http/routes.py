"""Phase 0 operational endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from codemind import __version__
from codemind.infrastructure.health import ReadinessCheck, evaluate_readiness

router = APIRouter(tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["codemind"] = "codemind"
    version: str = __version__


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, str]


@router.get("/health/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    """Report process liveness without touching external dependencies."""

    return LiveResponse()


@router.get("/health/ready", response_model=ReadyResponse)
async def ready(request: Request, response: Response) -> ReadyResponse:
    """Report whether required infrastructure is reachable."""

    checks: list[ReadinessCheck] = request.app.state.readiness_checks
    timeout: float = request.app.state.settings.readiness_timeout_seconds
    is_ready, results = await evaluate_readiness(checks, timeout)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="unavailable", checks=results)
    return ReadyResponse(status="ok", checks=results)


TraceId = Annotated[str | None, "X-Trace-ID response header"]
