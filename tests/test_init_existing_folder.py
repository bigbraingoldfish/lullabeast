"""Starting on a folder that isn't a git repo yet, which may already hold files.

Preflight auto-initializes such a folder, and Launch's Mode A does the same when
an API or MCP caller launches without preflight. Both commit everything in the
folder (``git add -A``) as the first commit, and every phase commit after that
runs ``git add .``, so the folder's ``.gitignore`` decides what lands in history.
A stray ``.env`` (secrets) was committed, and Mode A replaced an existing
``.gitignore`` outright, so files its rules kept out were committed too.

Mode A also wrote its scaffold before validating the roadmap, and on an invalid
one deleted the whole folder to clean up, files that were there before included.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ui.server import _run_init_project, _run_preflight_checks

pytestmark = pytest.mark.usefixtures("hermetic_deploy_profile")

VALID_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The user sees the thing happen.\n"
    "  - **How we'll check:** Run the thing; confirm output.\n"
    "  - **If this fails, the user sees:** Nothing happens.\n"
)

VALID_VERIFICATION_CONTENT = (
    "# Verification\n\n"
    "## Project type\n"
    "cli\n\n"
    "## Entry point\n"
    "- Command: `mycli --help`\n"
    "- Ready signal: process exits 0\n\n"
    "## Public surface\n"
    "1. Do the thing\n\n"
    "## Verification stack\n"
    "- Acceptance tool: subprocess + assertions\n"
)


def _tracked(repo: Path) -> set:
    out = subprocess.run(["git", "-C", str(repo), "ls-files"], check=True,
                         capture_output=True, text=True).stdout
    return set(out.split())


def _gitignore_lines(repo: Path) -> list:
    return [ln.strip() for ln in (repo / ".gitignore").read_text().splitlines()]


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "myproject"
    folder.mkdir()
    (folder / "app.py").write_text("print('hi')\n")
    (folder / ".env").write_text("API_KEY=not-for-git\n")
    return folder


def _launch(folder: Path, roadmap: str = VALID_ROADMAP_SEED) -> dict:
    with patch("ui.server._atomic_symlink_swap"):
        return _run_init_project(str(folder), roadmap,
                                 verification_content=VALID_VERIFICATION_CONTENT)


class TestLaunchModeA:
    def test_a_stray_env_stays_out_of_the_first_commit(self, tmp_path):
        folder = _folder(tmp_path)
        result = _launch(folder)
        assert result["ok"] is True, result
        tracked = _tracked(folder)
        assert "app.py" in tracked and ".env" not in tracked
        assert ".env" in _gitignore_lines(folder)
        assert (folder / ".env").read_text() == "API_KEY=not-for-git\n"

    def test_an_existing_gitignore_is_kept_and_extended(self, tmp_path):
        folder = _folder(tmp_path)
        (folder / ".gitignore").write_text("secrets.json\n")
        (folder / "secrets.json").write_text("{}\n")
        result = _launch(folder)
        assert result["ok"] is True, result
        lines = _gitignore_lines(folder)
        assert lines[0] == "secrets.json"
        assert ".autodev/pipeline/" in lines and ".env" in lines
        tracked = _tracked(folder)
        assert "secrets.json" not in tracked and ".env" not in tracked
        assert ".gitignore" in tracked

    def test_an_invalid_roadmap_deletes_nothing_and_writes_nothing(self, tmp_path):
        folder = _folder(tmp_path)
        before = sorted(p.name for p in folder.iterdir())
        result = _launch(folder, roadmap="# Not a roadmap\n")
        assert result["ok"] is False
        assert "Roadmap invalid" in result["error"]
        assert folder.is_dir()
        assert sorted(p.name for p in folder.iterdir()) == before
        assert (folder / "app.py").read_text() == "print('hi')\n"
        assert (folder / ".env").read_text() == "API_KEY=not-for-git\n"

    def test_a_failure_midway_removes_only_what_it_created(self, tmp_path):
        folder = _folder(tmp_path)
        before = sorted(p.name for p in folder.iterdir())
        import ui.server as server
        real_write = server.write_text_atomic

        def failing_write(path, content):
            if os.path.basename(path) == "prd.md":
                raise OSError("disk full")
            return real_write(path, content)

        with patch("ui.server.write_text_atomic", side_effect=failing_write):
            result = _launch(folder)
        assert result["ok"] is False and "disk full" in result["error"]
        assert folder.is_dir()
        assert sorted(p.name for p in folder.iterdir()) == before
        assert (folder / "app.py").read_text() == "print('hi')\n"


class TestPreflightAutoInit:
    def _config(self, repo: Path) -> dict:
        """Minimal OpenClaw root (symlink + agent workspaces), as in
        test_launch_dirty_repo.py, so the other checks find what they read."""
        oc = repo.parent / ".openclaw"
        oc.mkdir()
        (oc / "pipeline-project").symlink_to(repo)
        for agent in ("planner", "executor", "reviewer", "escalation"):
            ws = oc / f"workspace-{agent}"
            ws.mkdir()
            for doc in ("AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"):
                (ws / doc).write_text(f"# {doc}\n")
        pp = str(oc / "pipeline-project")
        return {
            "openclaw_root": str(oc),
            "project_dir_path": pp,
            "autodev_repo_path": str(repo),
            "autodev_pipeline_root": str(oc),
            "pipeline_state_path": str(oc / "pipeline_state.json"),
            "lock_path": str(oc / "pipeline.lock"),
            "pipeline_queue_path": str(oc / "pipeline_queue.json"),
            "events_path": str(oc / "pipeline_events.jsonl"),
            "ideas_dir": str(oc / "ideas"),
            "phase_state_path": os.path.join(pp, ".autodev", "pipeline", "phase_state.json"),
            "roadmap_path": os.path.join(pp, "roadmap.md"),
        }

    def test_a_stray_env_stays_out_of_the_first_commit(self, tmp_path):
        folder = _folder(tmp_path)
        checks = _run_preflight_checks(str(folder), config=self._config(folder))
        assert any(c["check"] == "git initial commit" and c["status"] == "fixed" for c in checks), checks
        tracked = _tracked(folder)
        assert "app.py" in tracked and ".env" not in tracked
        assert ".env" in _gitignore_lines(folder)
