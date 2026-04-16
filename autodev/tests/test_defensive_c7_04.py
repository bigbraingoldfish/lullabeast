"""C7-04: Orchestrator must validate OPENCLAW_ROOT and required workspace directories
at startup.  Missing dirs should produce per-item error messages + sys.exit(1),
not a confusing crash deep in the pipeline.

Checks:
  - OPENCLAW_ROOT directory exists
  - workspace-planner/, workspace-executor/, workspace-reviewer/ exist
  - openclaw.json present (belt-and-suspenders; also guarded by load_config)
"""
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_VALID_CONFIG = json.dumps({
    "hooks_url": "http://localhost:18789/hooks/agent",
    "hooks_token": "test-token",
})


class TestC704ValidateOpenclawRoot:

    def _setup(self, tmp_path, monkeypatch):
        """Reload orchestrator module with OPENCLAW_ROOT pointing to tmp_path."""
        monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
        import orchestrator as orch_mod
        importlib.reload(orch_mod)
        return orch_mod

    def _write_valid_config(self, tmp_path):
        (tmp_path / "openclaw.json").write_text(_VALID_CONFIG)

    def _create_workspaces(self, tmp_path):
        for role in ("planner", "executor", "reviewer"):
            (tmp_path / f"workspace-{role}").mkdir(exist_ok=True)

    def test_missing_workspace_planner_exits(self, tmp_path, monkeypatch):
        """sys.exit(1) when workspace-planner is absent."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._write_valid_config(tmp_path)
        # Create executor and reviewer but NOT planner
        (tmp_path / "workspace-executor").mkdir()
        (tmp_path / "workspace-reviewer").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            orch_mod._validate_openclaw_root(str(tmp_path))

        assert exc_info.value.code == 1, (
            "Expected sys.exit(1) for missing workspace-planner (C7-04 unfixed)"
        )

    def test_missing_workspace_executor_exits(self, tmp_path, monkeypatch):
        """sys.exit(1) when workspace-executor is absent."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._write_valid_config(tmp_path)
        (tmp_path / "workspace-planner").mkdir()
        (tmp_path / "workspace-reviewer").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            orch_mod._validate_openclaw_root(str(tmp_path))

        assert exc_info.value.code == 1

    def test_missing_workspace_reviewer_exits(self, tmp_path, monkeypatch):
        """sys.exit(1) when workspace-reviewer is absent."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._write_valid_config(tmp_path)
        (tmp_path / "workspace-planner").mkdir()
        (tmp_path / "workspace-executor").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            orch_mod._validate_openclaw_root(str(tmp_path))

        assert exc_info.value.code == 1

    def test_missing_openclaw_json_exits(self, tmp_path, monkeypatch):
        """sys.exit(1) when openclaw.json is absent (belt-and-suspenders)."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._create_workspaces(tmp_path)
        # No openclaw.json written

        with pytest.raises(SystemExit) as exc_info:
            orch_mod._validate_openclaw_root(str(tmp_path))

        assert exc_info.value.code == 1

    def test_all_present_does_not_exit(self, tmp_path, monkeypatch):
        """No sys.exit when all required dirs and openclaw.json are present."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._write_valid_config(tmp_path)
        self._create_workspaces(tmp_path)

        # Must not raise
        orch_mod._validate_openclaw_root(str(tmp_path))

    def test_validate_called_at_orchestrator_init(self, tmp_path, monkeypatch):
        """Orchestrator.__init__ must call _validate_openclaw_root so missing dirs
        are caught at construction time, not mid-run."""
        orch_mod = self._setup(tmp_path, monkeypatch)
        self._write_valid_config(tmp_path)
        # No workspace dirs → should exit(1) during Orchestrator()
        monkeypatch.setattr(orch_mod, "SkillManager", lambda _: MagicMock())

        with pytest.raises(SystemExit) as exc_info:
            orch_mod.Orchestrator()

        assert exc_info.value.code == 1, (
            "Expected Orchestrator() to call sys.exit(1) when workspace dirs are missing "
            "(C7-04 unfixed — _validate_openclaw_root not called from __init__)"
        )
