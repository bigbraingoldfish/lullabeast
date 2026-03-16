# Test infrastructure - infra1
import json
import os
import tempfile
from pathlib import Path


def test_import_ui_server():
    """Test that ui.server can be imported without errors."""


def test_load_config_default_returns_seven_keys():
    """Test load_config() with no args returns dict with exactly seven keys and expanded paths."""
    from ui.server import load_config
    result = load_config()
    
    expected_keys = [
        "port", "pipeline_state_path", "phase_state_path", 
        "lock_path", "events_path", "roadmap_path", "project_dir_path"
    ]
    
    assert isinstance(result, dict)
    assert set(result.keys()) == set(expected_keys), f"Keys mismatch: {set(result.keys())}"
    
    # All path values should be absolute (not starting with ~)
    path_keys = [k for k in result.keys() if k != "port"]
    for key in path_keys:
        assert not result[key].startswith("~"), f"{key} should have ~ expanded"
        assert result[key].startswith("/"), f"{key} should be absolute path"
    
    # Check default port
    assert result["port"] == 18790


def test_load_config_partial_override():
    """Test load_config() with a partial config file overrides only specified keys."""
    from ui.server import load_config
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"port": 9999}, f)
        temp_path = f.name
    
    try:
        result = load_config(config_path=temp_path)
        
        # port should be overridden
        assert result["port"] == 9999
        
        # All other keys should still be present with expanded defaults
        assert "pipeline_state_path" in result
        assert "phase_state_path" in result
        assert "lock_path" in result
        assert "events_path" in result
        assert "roadmap_path" in result
        assert "project_dir_path" in result
        
        # Paths should be expanded
        assert result["pipeline_state_path"].startswith("/")
    finally:
        os.unlink(temp_path)


def test_requirements_contains_fastapi_and_uvicorn():
    """Test that ui/requirements.txt contains fastapi and uvicorn."""
    req_path = Path(__file__).parent.parent / "ui" / "requirements.txt"
    content = req_path.read_text().lower()
    
    assert "fastapi" in content, "requirements.txt should contain fastapi"
    assert "uvicorn" in content, "requirements.txt should contain uvicorn"