from codemind.config import Settings
from codemind.interfaces.http.app import create_app


def test_openapi_contains_operational_endpoints() -> None:
    app = create_app(Settings(environment="test"))
    schema = app.openapi()

    assert schema["info"]["title"] == "CodeMind API"
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
    assert "/api/v1/repositories/{repository_id}/search" in schema["paths"]
    assert "/api/v1/repositories/{repository_id}/runs" in schema["paths"]
    assert "/api/v1/runs/{run_id}/events" in schema["paths"]
    assert "/api/v1/repositories/{repository_id}/memories" in schema["paths"]
