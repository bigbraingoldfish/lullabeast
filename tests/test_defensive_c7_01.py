"""C7-01: load_config must raise a clear RuntimeError on malformed config.json."""
import os
import sys
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.server import load_config


def test_load_config_raises_on_invalid_json(tmp_path):
    """A corrupt config.json (invalid JSON) must raise RuntimeError with a
    human-readable message pointing at the file — not a raw JSONDecodeError
    traceback deep inside json.load."""
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{ this is not valid JSON !!!", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        load_config(config_path=str(bad_config))

    msg = str(exc_info.value)
    # Must mention the file path so the operator knows where to look.
    assert str(bad_config) in msg, (
        f"Error message does not contain the config file path.\nGot: {msg}"
    )


def test_load_config_raises_on_truncated_json(tmp_path):
    """Truncated JSON (e.g. from a crashed mid-write) must also raise RuntimeError."""
    bad_config = tmp_path / "config.json"
    bad_config.write_text('{"hooks_url": "http://localhost:18789', encoding="utf-8")

    with pytest.raises(RuntimeError):
        load_config(config_path=str(bad_config))


def test_load_config_succeeds_on_valid_json(tmp_path):
    """Sanity: valid config.json still loads without exception."""
    good_config = tmp_path / "config.json"
    good_config.write_text(json.dumps({"hooks_url": "http://localhost:18789/hooks/agent"}))

    config = load_config(config_path=str(good_config))
    assert config.get("hooks_url") == "http://localhost:18789/hooks/agent"


def test_load_config_absent_config_file_uses_defaults(tmp_path):
    """If config.json does not exist, defaults are returned without exception."""
    missing = tmp_path / "nonexistent_config.json"
    assert not missing.exists()

    config = load_config(config_path=str(missing))
    assert isinstance(config, dict)
