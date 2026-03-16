"""Tests for INFRA-2: FastAPI server health endpoint."""
import sys
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from ui.server import app


def test_health_returns_ok_true():
    """GET /health returns 200 with {"ok": true}."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}