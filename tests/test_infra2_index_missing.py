"""Tests for INFRA-2: FastAPI server root endpoint without index.html."""
import sys
sys.path.insert(0, '.')

import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from ui.server import app


@pytest.fixture
def no_index_html():
    """Ensure index.html is absent during test; restore it afterwards."""
    ui_dir = Path(__file__).parent.parent / "ui"
    index_path = ui_dir / "index.html"

    existed = index_path.exists()
    original_content = index_path.read_bytes() if existed else None

    if existed:
        index_path.unlink()

    yield index_path

    if existed and original_content is not None:
        index_path.write_bytes(original_content)
    elif not existed and index_path.exists():
        index_path.unlink()


def test_root_without_index_html_returns_404(no_index_html):
    """GET / returns 404 when index.html is absent."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 404
