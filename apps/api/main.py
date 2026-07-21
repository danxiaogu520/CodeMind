"""ASGI module for `uvicorn apps.api.main:app`."""

from codemind.interfaces.http.app import create_app

app = create_app()
