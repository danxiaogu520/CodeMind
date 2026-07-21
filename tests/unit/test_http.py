from httpx import AsyncClient


async def test_trace_id_is_preserved(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Trace-ID": "test-trace-01"})

    assert response.headers["X-Trace-ID"] == "test-trace-01"


async def test_invalid_trace_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Trace-ID": "bad trace id"})

    assert response.headers["X-Trace-ID"] != "bad trace id"
    assert len(response.headers["X-Trace-ID"]) == 32


async def test_not_found_uses_problem_details(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist", headers={"X-Trace-ID": "trace-404"})

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "HTTP error",
        "status": 404,
        "detail": "Not Found",
        "instance": "/does-not-exist",
        "trace_id": "trace-404",
    }


async def test_browser_demo_is_packaged_with_security_headers(client: AsyncClient) -> None:
    response = await client.get("/demo")

    assert response.status_code == 200
    assert "CodeMind · Repository Intelligence" in response.text
    assert "memory.recalled" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]
