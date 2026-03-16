"""Tests for INFRA-2: FastAPI UI server - comprehensive test suite."""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from fastapi.testclient import TestClient
from ui.server import app, load_config


def test_health_returns_ok_true():
    """GET /health returns HTTP 200 with body {"ok": true}."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_root_with_index_html_present():
    """GET / with ui/index.html present returns HTTP 200 with Content-Type: text/html."""
    ui_dir = Path(__file__).parent.parent / "ui"
    index_path = ui_dir / "index.html"
    
    # Save original state
    original_exists = index_path.exists()
    original_content = None
    if original_exists:
        original_content = index_path.read_text()
    
    try:
        # Create temporary index.html
        index_path.write_text("<html><body>Test</body></html>")
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
    finally:
        # Restore original state
        if original_exists and original_content:
            index_path.write_text(original_content)
        elif index_path.exists():
            index_path.unlink()


def test_root_without_index_html_returns_404():
    """GET / with ui/index.html absent returns HTTP 404."""
    ui_dir = Path(__file__).parent.parent / "ui"
    index_path = ui_dir / "index.html"
    
    # Save original state
    original_exists = index_path.exists()
    original_content = None
    if original_exists:
        original_content = index_path.read_text()
        index_path.unlink()
    
    try:
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 404
    finally:
        # Restore original state
        if original_exists and original_content:
            index_path.write_text(original_content)


def test_server_starts_without_config():
    """Server starts successfully when ui/config.json is absent (uses defaults)."""
    ui_dir = Path(__file__).parent.parent / "ui"
    config_path = ui_dir / "config.json"
    
    # Save original state
    original_exists = config_path.exists()
    original_content = None
    if original_exists:
        original_content = config_path.read_text()
        config_path.unlink()
    
    try:
        # Reload config to verify it works without config.json
        from importlib import reload
        import ui.server
        reload(ui.server)
        
        config = ui.server.load_config()
        
        assert "port" in config
        assert config["port"] == 18790
        assert "pipeline_state_path" in config
        assert "phase_state_path" in config
        assert "lock_path" in config
        assert "events_path" in config
        assert "roadmap_path" in config
        assert "project_dir_path" in config
        
        # Verify app still works
        from fastapi import FastAPI
        assert isinstance(ui.server.app, FastAPI)
        
    finally:
        # Restore original config
        if original_exists and original_content:
            config_path.write_text(original_content)