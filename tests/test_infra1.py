# Test infrastructure - infra1
import json
import os
import tempfile
from pathlib import Path


def test_import_ui_server():
    """Test that ui.server can be imported without errors."""


def test_load_config_default_returns_seven_keys():
    """Test load_config() with no args returns merged DEFAULTS + config.json keys and expanded paths."""
    from ui.server import DEFAULTS, load_config
    result = load_config()

    assert isinstance(result, dict)
    # At minimum all DEFAULTS keys; config.json may add e.g. autodev_repo_path
    assert set(DEFAULTS.keys()).issubset(set(result.keys())), (
        f"Missing keys: {set(DEFAULTS.keys()) - set(result.keys())}"
    )
    
    # Path-like string values should have ~ expanded to absolute paths (skip URLs and secrets)
    non_path_keys = {"port", "hooks_url", "hooks_token", "ui_token", "base_branch", "log_level"}
    path_keys = [k for k in result.keys() if k not in non_path_keys]
    for key in path_keys:
        val = result[key]
        if not isinstance(val, str):
            continue
        assert not val.startswith("~"), f"{key} should have ~ expanded"
        assert val.startswith("/") or val.startswith("http"), f"{key} should be absolute path or URL"
    
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


def test_load_config_autodev_hooks_token_env_overrides_file():
    """AUTODEV_HOOKS_TOKEN overrides hooks_token from JSON (secrets not only in file)."""
    from ui.server import load_config

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"hooks_token": "from-file"}, f)
        temp_path = f.name

    old = os.environ.get("AUTODEV_HOOKS_TOKEN")
    try:
        os.environ["AUTODEV_HOOKS_TOKEN"] = "from-env"
        result = load_config(config_path=temp_path)
        assert result["hooks_token"] == "from-env"
    finally:
        os.unlink(temp_path)
        if old is None:
            os.environ.pop("AUTODEV_HOOKS_TOKEN", None)
        else:
            os.environ["AUTODEV_HOOKS_TOKEN"] = old


def test_requirements_contains_fastapi_and_uvicorn():
    """Test that ui/requirements.txt contains fastapi and uvicorn."""
    req_path = Path(__file__).parent.parent / "ui" / "requirements.txt"
    content = req_path.read_text().lower()
    
    assert "fastapi" in content, "requirements.txt should contain fastapi"
    assert "uvicorn" in content, "requirements.txt should contain uvicorn"