"""C4-03: SkillManager._clean_workspace_skills must fail closed on rmtree failure.

If shutil.rmtree fails the stale skills directory is still present. Proceeding
to copy a new skill into a dirty tree is worse than no injection at all.
The fix: on rmtree failure, return early from inject_skill with Status=clean_failed.
"""
import os
import sys
import shutil
import importlib
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_REPO_PATH", str(REPO_ROOT))

    import skill_manager as sm_mod
    importlib.reload(sm_mod)

    mgr = sm_mod.SkillManager.__new__(sm_mod.SkillManager)
    mgr._workspace_dir = str(tmp_path)
    mgr._skill_library_dir = os.path.join(REPO_ROOT, "autodev", "skill-library")
    mgr._mapping_file = os.path.join(REPO_ROOT, "autodev", "config", "skill_mapping.yaml")
    mgr._mapping = {"CORE": "core-logic"}  # minimal mapping

    return mgr, sm_mod, tmp_path


class TestC403CleanWorkspaceSkillsFailClosed:

    def test_rmtree_failure_prevents_skill_copy(self, mgr, tmp_path):
        """When rmtree raises OSError, inject_skill must not copy any skill file."""
        inst, mod, base = mgr

        # Set up a real source skill so it WOULD be copied if clean succeeded
        source_skill_dir = base / "skill-library" / "core-logic" / "executor"
        source_skill_dir.mkdir(parents=True)
        (source_skill_dir / "SKILL.md").write_text("# Skill content")

        # Override skill library dir to our test one
        inst._skill_library_dir = str(base / "skill-library")

        dest_skills_dir = base / "workspace-executor" / "skills"
        dest_skills_dir.mkdir(parents=True)
        # Put a stale file in there to prove it wasn't cleaned
        (dest_skills_dir / "stale_file.txt").write_text("stale")

        openclaw_config = {}

        captured_logs = []
        original_log = mod.SkillManager._log.__func__ if hasattr(mod.SkillManager._log, '__func__') else mod.SkillManager._log

        with patch("shutil.rmtree", side_effect=OSError("Permission denied")), \
             patch.object(mod.SkillManager, "_log", staticmethod(lambda *a, **kw: captured_logs.append(a))):
            inst.inject_skill("CORE-E1", "executor", openclaw_config)

        # No new skill directory should exist under skills/
        new_skill_dir = dest_skills_dir / "core-logic-executor"
        assert not new_skill_dir.exists(), (
            "Skill was injected into a dirty workspace after rmtree failed — "
            "stale content could mislead the agent."
        )

    def test_rmtree_failure_logs_clean_failed(self, mgr, tmp_path):
        """When rmtree fails, a Status=clean_failed log line must be emitted."""
        inst, mod, base = mgr

        source_skill_dir = base / "skill-library" / "core-logic" / "executor"
        source_skill_dir.mkdir(parents=True)
        (source_skill_dir / "SKILL.md").write_text("# Skill content")
        inst._skill_library_dir = str(base / "skill-library")

        dest_skills_dir = base / "workspace-executor" / "skills"
        dest_skills_dir.mkdir(parents=True)

        captured_logs = []

        with patch("shutil.rmtree", side_effect=OSError("no perms")), \
             patch.object(mod.SkillManager, "_log",
                          staticmethod(lambda *a, **kw: captured_logs.append(a))):
            inst.inject_skill("CORE-E1", "executor", {})

        # At least one log entry should mention clean_failed
        log_text = " ".join(str(a) for entry in captured_logs for a in entry)
        assert "clean_failed" in log_text, (
            f"Expected 'clean_failed' in skill log output; got: {log_text!r}"
        )

    def test_clean_success_still_injects(self, mgr, tmp_path):
        """Sanity: when rmtree succeeds, skill is still injected normally."""
        inst, mod, base = mgr

        source_skill_dir = base / "skill-library" / "core-logic" / "executor"
        source_skill_dir.mkdir(parents=True)
        (source_skill_dir / "SKILL.md").write_text("# Skill content")
        inst._skill_library_dir = str(base / "skill-library")

        dest_skills_dir = base / "workspace-executor" / "skills"
        dest_skills_dir.mkdir(parents=True)

        inst.inject_skill("CORE-E1", "executor", {})

        assert (dest_skills_dir / "core-logic-executor" / "SKILL.md").exists()
