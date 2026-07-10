# Test infrastructure - infra1
import json
import os
import tempfile
from pathlib import Path


def test_import_ui_server():
    """Test that ui.server can be imported without errors."""


def _seeded_config():
    """Raw ui/config.json next to the server module — the deployment profile
    (public stack: absent/sparse; dev stack: container-seeded with remapped
    ports). Reading it directly lets assertions on load_config() be
    deployment-aware instead of hardcoding one stack's ports."""
    import ui.server
    cfg_path = Path(ui.server.__file__).parent / "config.json"
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        return json.load(f)


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
    # provider_key_path / setup_marker_path / projects_dir default to "" (unset on bare
    # metal, container-seeded via ui/config.json), same as base_branch — they are only
    # absolute when configured, so an empty default is not a "~ not expanded" bug.
    # local_model_probe_host is a bare hostname (container: host.docker.internal),
    # never a path or URL.
    non_path_keys = {
        "port", "hooks_url", "hooks_token", "ui_token", "base_branch", "log_level",
        "provider_key_path", "setup_marker_path", "projects_dir",
        "local_model_probe_host",
    }
    path_keys = [k for k in result.keys() if k not in non_path_keys]
    for key in path_keys:
        val = result[key]
        if not isinstance(val, str):
            continue
        assert not val.startswith("~"), f"{key} should have ~ expanded"
        assert val.startswith("/") or val.startswith("http"), f"{key} should be absolute path or URL"

    # Port follows the deployment profile: the seeded override when present
    # (dev stack remaps to 28790 so it can run beside a public stack on
    # 18790), the DEFAULTS port otherwise.
    assert result["port"] == int(_seeded_config().get("port", DEFAULTS["port"]))


def test_load_config_port_profiles_public_and_dev():
    """Both deployment profiles resolve their documented ports, on any machine.

    Public/bare-metal (no config.json): UI 18790, gateway reached on 18789.
    Dev stack (container-seeded config.json): UI 28790 with
    gateway_published_port 28789, so both stacks run side by side without
    port conflicts. Hermetic — pins the profile pairs without depending on
    which stack this machine happens to run.
    """
    from ui.server import DEFAULTS, load_config

    # Public profile: pure defaults (a config path that does not exist).
    with tempfile.TemporaryDirectory() as td:
        public = load_config(config_path=os.path.join(td, "absent.json"))
    assert public["port"] == 18790 == DEFAULTS["port"]
    assert "gateway_published_port" not in public

    # Dev profile: the container entrypoint seeds the remapped ports.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"port": 28790, "gateway_published_port": 28789}, f)
        dev_path = f.name
    try:
        dev = load_config(config_path=dev_path)
    finally:
        os.unlink(dev_path)
    assert dev["port"] == 28790
    assert dev["gateway_published_port"] == 28789


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