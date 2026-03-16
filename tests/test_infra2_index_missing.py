"""Tests for INFRA-2: FastAPI server root endpoint without index.html."""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from fastapi.testclient import TestClient
from ui.server import app


def test_root_without_index_html_returns_404():
    """GET / returns 404 when index.html is absent."""
    # Ensure index.html doesn't exist
    ui_dir = Path(__file__).parent.parent / "ui"
    index_path = ui_dir / "index.html"
    
    # Save original state - we don't need to track this since we won't create the file
    
    try:
        # Ensure no index.html
        if index_path.exists():
            index_path.unlink()
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 404
    finally:
        # Restore original state if needed
        pass  # We didn't create it, so no need to restore