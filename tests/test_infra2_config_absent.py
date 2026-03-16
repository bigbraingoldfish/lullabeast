"""Tests for INFRA-2: FastAPI server starts without config.json."""
import sys
sys.path.insert(0, '.')

from pathlib import Path


def test_server_starts_without_config():
    """Server starts correctly when ui/config.json is absent (uses defaults)."""
    # This test verifies that load_config works without config.json
    # and that the app can be imported without errors
    
    from ui.server import app, load_config
    
    # Save original config path
    ui_dir = Path(__file__).parent.parent / "ui"
    config_path = ui_dir / "config.json"
    
    # Save original state
    original_exists = config_path.exists()
    original_content = None
    if original_exists:
        original_content = config_path.read_text()
    
    try:
        # Remove config.json if it exists
        if config_path.exists():
            config_path.unlink()
        
        # Verify load_config still works with defaults
        config = load_config()
        
        assert "port" in config
        assert config["port"] == 18790
        assert "pipeline_state_path" in config
        assert "phase_state_path" in config
        assert "lock_path" in config
        assert "events_path" in config
        assert "roadmap_path" in config
        assert "project_dir_path" in config
        
        # Verify app exists and is a FastAPI instance
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)
        
    finally:
        # Restore original config if it existed
        if original_exists and original_content:
            config_path.write_text(original_content)