"""RFC 9457-style problem details and exception handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Problem:
    status: int
    title: str
    detail: str
    type: str = "about:blank"


def _response(request: Request, problem: Problem, **extensions: Any) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    body: dict[str, Any] = {
        "type": problem.type,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        **extensions,
    }
    if trace_id is not None:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=problem.status, media_type="application/problem+json")


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    return _response(
        request,
        Problem(status=exc.status_code, title="HTTP error", detail=str(exc.detail)),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return _response(
        request,
        Problem(status=422, title="Request validation failed", detail="Invalid request data."),
        errors=errors,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    await logger.aexception("unhandled_request_exception", error_type=type(exc).__name__)
    return _response(
        request,
        Problem(status=500, title="Internal server error", detail="An unexpected error occurred."),
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
