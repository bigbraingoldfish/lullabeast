"""C3-05: pipeline_state.json must not be written to disk when spawn fails.

Both call sites: /api/setup/launch and /api/setup/switch-project.
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_subprocess_pass():
    def _inner(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = b""
        return m
    return _inner


VALID_ROADMAP = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The thing works.\n"
    "  - **How we'll check:** Run it.\n"
    "  - **If this fails, the user sees:** Nothing.\n"
)

VALID_VERIFICATION = (
    "# Verification\n\n"
    "## Project type\ncli\n\n"
    "## Entry point\n- Command: `x`\n- Ready signal: ok\n\n"
    "## Public surface\n1. do thing\n\n"
    "## Verification stack\n- Acceptance tool: subprocess + assertions\n"
)


class TestC305LaunchStateNotWrittenOnSpawnFail:
    """Site 2: POST /api/setup/launch."""

    def test_state_file_absent_when_spawn_fails(self, tmp_path):
        """pipeline_state.json must NOT exist on disk when _spawn_orchestrator returns ok=False."""
        from fastapi.testclient import TestClient
        from ui.server import app

        state_file = tmp_path / "pipeline_state.json"
        orch_dir = tmp_path / "orch"
        orch_dir.mkdir()

        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(orch_dir),
        }

        client = TestClient(app)
        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server._atomic_symlink_swap"), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._spawn_orchestrator",
                   return_value={"ok": False, "error": "mock spawn failure"}):
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(tmp_path / "proj"), "roadmap_seed": VALID_ROADMAP, "verification_content": VALID_VERIFICATION},
            )

        data = response.json()
        assert data.get("ok") is False, f"Expected ok=False, got: {data}"
        assert not state_file.exists(), (
            "pipeline_state.json was written to disk even though spawn failed; "
            "this leaves a stale RUNNING state with no orchestrator process."
        )

    def test_state_file_written_when_spawn_succeeds(self, tmp_path):
        """Sanity-check: pipeline_state.json IS written when spawn succeeds."""
        from fastapi.testclient import TestClient
        from ui.server import app

        state_file = tmp_path / "pipeline_state.json"
        orch_dir = tmp_path / "orch"
        orch_dir.mkdir()

        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(orch_dir),
        }

        client = TestClient(app)
        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server._atomic_symlink_swap"), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._spawn_orchestrator",
                   return_value={"ok": True, "error": None}):
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(tmp_path / "proj"), "roadmap_seed": VALID_ROADMAP, "verification_content": VALID_VERIFICATION},
            )

        data = response.json()
        assert data.get("ok") is True, f"Expected ok=True, got: {data}"
        assert state_file.exists(), "pipeline_state.json should be written when spawn succeeds."


class TestC305SwitchProjectStateNotWrittenOnSpawnFail:
    """Site 1: POST /api/setup/switch-project."""

    def test_state_file_absent_when_spawn_fails(self, tmp_path):
        """pipeline_state.json must NOT be left on disk when spawn fails in switch-project."""
        from fastapi.testclient import TestClient
        from ui.server import app

        proj = tmp_path / "proj"
        proj.mkdir()
        # Minimal git repo so preflight passes enough to reach the spawn step
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text(VALID_ROADMAP)

        state_file = tmp_path / "state" / "pipeline_state.json"
        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(tmp_path / "orch"),
            "project_dir_path": str(tmp_path / "pipeline-project"),
        }

        client = TestClient(app)
        with patch("ui.server._project_switch_allowed", return_value=(True, "STOPPED")), \
             patch("ui.server._run_preflight_checks", return_value=[]), \
             patch("ui.server._preflight_materialize", return_value=[]), \
             patch("ui.server._validate_project_coherence", return_value={"ok": True}), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._glob_project_roadmap_paths", return_value=[str(proj / "roadmap.md")]), \
             patch("ui.server.append_recent_project"), \
             patch("ui.server._spawn_orchestrator",
                   return_value={"ok": False, "error": "mock spawn failure"}):
            response = client.post(
                "/api/setup/switch-project",
                json={
                    "repo_path": str(proj),
                    "roadmap_seed": VALID_ROADMAP,
                    "start_orchestrator": True,
                },
            )

        data = response.json()
        assert data.get("ok") is False, f"Expected ok=False, got: {data}"
        assert not state_file.exists(), (
            "pipeline_state.json was written before spawn completed in switch-project; "
            "a failed spawn leaves stale RUNNING state."
        )

    def test_revive_path_state_preserved_when_spawn_fails(self, tmp_path):
        """C3-05 guard: the --revive path SKIPS the pre-spawn write (it resumes a parked
        entry's escalated-phase state), so a spawn failure must NOT delete the existing
        pipeline_state.json. Locks the `_revive_id is None` condition on the rollback — an
        unconditional rollback would silently destroy a parked escalation's state on a
        transient spawn failure.
        """
        from fastapi.testclient import TestClient
        from ui.server import app

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text(VALID_ROADMAP)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        state_file = state_dir / "pipeline_state.json"
        # Pre-existing parked-escalation state that the revive path must preserve.
        sentinel = '{"pipeline_status": "WAITING_FOR_HUMAN", "current_phase_raw_id": "TEST-E1"}'
        state_file.write_text(sentinel)

        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(tmp_path / "orch"),
            "project_dir_path": str(tmp_path / "pipeline-project"),
        }

        client = TestClient(app)
        with patch("ui.server._project_switch_allowed", return_value=(True, "STOPPED")), \
             patch("ui.server._run_preflight_checks", return_value=[]), \
             patch("ui.server._preflight_materialize", return_value=[]), \
             patch("ui.server._validate_project_coherence", return_value={"ok": True}), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._glob_project_roadmap_paths", return_value=[str(proj / "roadmap.md")]), \
             patch("ui.server.append_recent_project"), \
             patch("ui.server._queue_entry_for_project",
                   return_value={"id": "entry-1", "state": "ESCALATION"}), \
             patch("ui.server._entry_is_parked_escalation", return_value=True), \
             patch("ui.server._spawn_orchestrator",
                   return_value={"ok": False, "error": "mock spawn failure"}):
            response = client.post(
                "/api/setup/switch-project",
                json={
                    "repo_path": str(proj),
                    "roadmap_seed": VALID_ROADMAP,
                    "start_orchestrator": True,
                },
            )

        data = response.json()
        assert data.get("ok") is False, f"Expected ok=False, got: {data}"
        assert state_file.exists(), (
            "revive-path spawn failure must NOT delete the pre-existing parked-escalation state."
        )
        assert state_file.read_text() == sentinel, (
            "revive-path state must be left byte-for-byte intact (the write is skipped, "
            "so the rollback must be skipped too)."
        )
