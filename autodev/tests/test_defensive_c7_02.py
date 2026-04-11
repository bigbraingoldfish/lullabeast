"""C7-02: Orchestrator load_config must fail fast when required keys
(hooks_url, hooks_token) are missing from openclaw.json.

Without the fix, the orchestrator starts successfully, consumes retry budget
and time, then surfaces AUTH_ERROR minutes later with no clear diagnostic.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _minimal_valid_config():
    """Minimum config that passes the required-key check."""
    return {"hooks_url": "http://localhost:18789/hooks/agent", "hooks_token": "test-token"}


class TestC702LoadConfigFailFast:

    def test_missing_hooks_url_calls_sys_exit(self, tmp_path, monkeypatch):
        """load_config must call sys.exit(1) when hooks_url is absent."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        # Write config without hooks_url
        (tmp_path / "openclaw.json").write_text(
            json.dumps({"hooks_token": "secret"})
        )

        inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        with pytest.raises(SystemExit) as exc_info:
            orch_mod.Orchestrator.load_config(inst)

        assert exc_info.value.code == 1, (
            "Expected sys.exit(1) when hooks_url is missing from openclaw.json (C7-02 unfixed)"
        )

    def test_missing_hooks_token_calls_sys_exit(self, tmp_path, monkeypatch):
        """load_config must call sys.exit(1) when hooks_token is absent."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        # Write config without hooks_token
        (tmp_path / "openclaw.json").write_text(
            json.dumps({"hooks_url": "http://localhost:18789/hooks/agent"})
        )

        inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        with pytest.raises(SystemExit) as exc_info:
            orch_mod.Orchestrator.load_config(inst)

        assert exc_info.value.code == 1, (
            "Expected sys.exit(1) when hooks_token is missing from openclaw.json (C7-02 unfixed)"
        )

    def test_empty_config_calls_sys_exit(self, tmp_path, monkeypatch):
        """load_config must call sys.exit(1) when openclaw.json is {} (both keys absent)."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        (tmp_path / "openclaw.json").write_text("{}")

        inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        with pytest.raises(SystemExit) as exc_info:
            orch_mod.Orchestrator.load_config(inst)

        assert exc_info.value.code == 1, (
            "Expected sys.exit(1) for empty openclaw.json (C7-02 unfixed)"
        )

    def test_valid_config_returns_dict(self, tmp_path, monkeypatch):
        """Sanity: a config with both required keys must be returned without exiting."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        (tmp_path / "openclaw.json").write_text(json.dumps(_minimal_valid_config()))

        inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        result = orch_mod.Orchestrator.load_config(inst)

        assert result.get("hooks_url") == "http://localhost:18789/hooks/agent"
        assert result.get("hooks_token") == "test-token"

    def test_missing_file_calls_sys_exit(self, tmp_path, monkeypatch):
        """load_config must still exit(1) when openclaw.json does not exist."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        # No openclaw.json written
        assert not (tmp_path / "openclaw.json").exists()

        inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        with pytest.raises(SystemExit) as exc_info:
            orch_mod.Orchestrator.load_config(inst)

        assert exc_info.value.code == 1
