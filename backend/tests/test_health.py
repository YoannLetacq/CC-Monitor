"""Smoke tests for the CC-monitor backend health endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_200() -> None:
    """GET /health must return HTTP 200."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_payload() -> None:
    """GET /health must return {"status": "ok"}."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.json() == {"status": "ok"}
