"""C7-03: SkillManager must write skill_health.json to AUTODEV_ROOT at construction
time so operators can check skill-injection readiness without reading logs.

The file should describe:
  - whether PyYAML is available
  - whether the mapping file was loaded
  - how many mappings were loaded
  - the mapping file path

No fail-fast: graceful degradation is intentional. This file just surfaces status.
"""
import json
import os
import sys
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestC703SkillHealth:

    def test_skill_health_json_written_on_init(self, tmp_path, monkeypatch):
        """SkillManager.__init__ must write skill_health.json to AUTODEV_ROOT."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))

        import skill_manager as sm_mod
        importlib.reload(sm_mod)

        sm = sm_mod.SkillManager(str(tmp_path))

        health_file = tmp_path / "skill_health.json"
        assert health_file.exists(), (
            "skill_health.json was not written to AUTODEV_ROOT on SkillManager init "
            "(C7-03 unfixed)"
        )

    def test_skill_health_json_contains_expected_fields(self, tmp_path, monkeypatch):
        """skill_health.json must contain yaml_available, mapping_loaded, mapping_count."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))

        import skill_manager as sm_mod
        importlib.reload(sm_mod)

        sm = sm_mod.SkillManager(str(tmp_path))

        health = json.loads((tmp_path / "skill_health.json").read_text())
        assert "yaml_available" in health, f"Missing 'yaml_available' in skill_health.json: {health}"
        assert "mapping_loaded" in health, f"Missing 'mapping_loaded' in skill_health.json: {health}"
        assert "mapping_count" in health, f"Missing 'mapping_count' in skill_health.json: {health}"
        assert "mapping_file" in health, f"Missing 'mapping_file' in skill_health.json: {health}"

    def test_skill_health_yaml_unavailable_reflected(self, tmp_path, monkeypatch):
        """When PyYAML is not installed, yaml_available must be False in health file."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))

        import skill_manager as sm_mod
        importlib.reload(sm_mod)

        # Simulate PyYAML unavailable
        with patch.object(sm_mod, "_YAML_AVAILABLE", False):
            # Need to re-init with the patched value
            sm = sm_mod.SkillManager.__new__(sm_mod.SkillManager)
            sm._workspace_dir = str(tmp_path)
            sm._skill_library_dir = os.path.join(REPO_ROOT, "autodev", "skill-library")
            sm._mapping_file = os.path.join(REPO_ROOT, "autodev", "config", "skill_mapping.yaml")
            sm._mapping = {}
            sm._write_health_file()  # call directly

        health = json.loads((tmp_path / "skill_health.json").read_text())
        assert health["yaml_available"] is False

    def test_skill_health_with_real_mapping_loaded(self, tmp_path, monkeypatch):
        """When the real mapping file is present, mapping_loaded must be True and count > 0."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        # Point AUTODEV_REPO_PATH to the real repo so the real mapping file is found
        monkeypatch.setenv("AUTODEV_REPO_PATH", REPO_ROOT)

        import skill_manager as sm_mod
        importlib.reload(sm_mod)

        sm = sm_mod.SkillManager(str(tmp_path))

        health = json.loads((tmp_path / "skill_health.json").read_text())
        assert health["mapping_loaded"] is True, f"Expected mapping_loaded=True, got: {health}"
        assert health["mapping_count"] > 0, f"Expected mapping_count > 0, got: {health}"
