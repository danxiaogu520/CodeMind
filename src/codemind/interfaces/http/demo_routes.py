"""Self-contained browser demo shipped with the API."""

from __future__ import annotations

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/demo", status_code=307)


@router.get("/demo", response_class=HTMLResponse)
async def demo() -> HTMLResponse:
    html = files("codemind.interfaces.http.static").joinpath("demo.html").read_text("utf-8")
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
