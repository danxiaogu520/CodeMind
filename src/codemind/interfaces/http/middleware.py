"""HTTP middleware for request correlation and access logs."""

from __future__ import annotations

import re
import time
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = structlog.get_logger(__name__)
TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_trace_id = request.headers.get("X-Trace-ID", "")
        trace_id = (
            supplied_trace_id if TRACE_ID_PATTERN.fullmatch(supplied_trace_id) else uuid4().hex
        )
        request.state.trace_id = trace_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            await logger.ainfo(
                "http_request",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
        response.headers["X-Trace-ID"] = trace_id
        return response
