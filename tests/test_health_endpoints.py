"""Tests for the /health and /ready HTTP endpoints."""

from starlette.testclient import TestClient

from dial_openapi_to_mcp.server import mcp


def test_health_returns_ok():
    app = mcp.http_app(path="/mcp", transport="streamable-http")
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_ready_returns_ready_after_startup():
    app = mcp.http_app(path="/mcp", transport="streamable-http")
    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}
