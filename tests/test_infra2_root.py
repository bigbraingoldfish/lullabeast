"""Tests for INFRA-2: FastAPI server root endpoint with index.html present."""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from fastapi.testclient import TestClient
from ui.server import app


def test_root_with_index_html_present(tmp_path):
    """GET / returns 200 with Content-Type: text/html when index.html exists."""
    # Create a temporary index.html in the ui directory
    ui_dir = Path(__file__).parent.parent / "ui"
    index_path = ui_dir / "index.html"

    # Save original state (must restore content — same pattern as test_server.py)
    original_exists = index_path.exists()
    original_content = None
    if original_exists:
        original_content = index_path.read_text()

    try:
        # Create temporary index.html
        index_path.write_text("<html></html>")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
    finally:
        # Restore original state
        if original_exists and original_content is not None:
            index_path.write_text(original_content)
        elif index_path.exists():
            index_path.unlink()