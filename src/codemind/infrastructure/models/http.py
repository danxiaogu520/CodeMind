"""Small async JSON client built on the standard library."""

from __future__ import annotations

import asyncio
import json
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelServiceError(RuntimeError):
    """Raised when a configured model service is unavailable or returns invalid data."""


async def post_json(url: str, payload: dict[str, object], timeout_seconds: float) -> object:
    return await asyncio.to_thread(_request_json, "POST", url, payload, timeout_seconds)


async def get_json(url: str, timeout_seconds: float) -> object:
    return await asyncio.to_thread(_request_json, "GET", url, None, timeout_seconds)


def _request_json(
    method: str, url: str, payload: dict[str, object] | None, timeout: float
) -> object:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(  # noqa: S310 - URL is validated operator configuration
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise ModelServiceError(f"Model service returned HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise ModelServiceError("Model service is unavailable.") from exc
    try:
        return cast(object, json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelServiceError("Model service returned invalid JSON.") from exc
