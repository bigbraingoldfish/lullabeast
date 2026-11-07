"""Launching on an existing repo with uncommitted work stops before it writes.

The pipeline commits with ``git add .`` at each phase and resets the working
tree with ``git reset --hard`` when it retries, so uncommitted work in the repo
would be swept into its commits or reset away. Mode B init used to sweep it
into "init: add pipeline project structure", untracked secrets included, and
committing only init's own files left the rest exposed to those resets.

The Launch dialog's preflight now lists the uncommitted paths in a failing
check, which holds Launch and Add to Queue in the dashboard, and init refuses
with the same message before writing anything (API callers skip preflight).
Adding a repo to the queue starts it the same way, so queue add refuses too:
the Queue screen's Add dialog and the MCP queue_add tool post it directly.
The shared preflight checks that switch-project, revalidate and relaunch run
stay as they were: those resume runs whose own work in progress is
uncommitted.

Lullabeast's own files don't count: preflight writes the roadmap, PRD,
verification doc, .gitignore entries and .env.example before launch, archives
extra roadmaps under autodev_archive/, and keeps pipeline state under
.autodev/; init commits those.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ui.server import _run_init_project, _run_preflight_checks, _uncommitted_user_changes

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

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=_ENV).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myproject"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "README.md").write_text("# Test\n")
    (repo / "app.py").write_text("print('v1')\n")
    (repo / "old-roadmap.md").write_text("# old\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


class TestUncommittedUserChanges:
    def test_clean_repo(self, tmp_path):
        assert _uncommitted_user_changes(str(_repo(tmp_path))) == []

    def test_lists_modified_staged_and_untracked_work(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        (repo / "notes.txt").write_text("todo\n")
        (repo / "lib").mkdir()
        (repo / "lib" / "util.py").write_text("x = 1\n")
        _git(repo, "add", "lib/util.py")
        assert sorted(_uncommitted_user_changes(str(repo))) == ["app.py", "lib/util.py", "notes.txt"]

    def test_lullabeast_files_do_not_count(self, tmp_path):
        """What preflight and init write before the check runs again at launch."""
        repo = _repo(tmp_path)
        for name in ("roadmap.md", "prd.md", "verification.md", ".gitignore", ".env.example"):
            (repo / name).write_text("x\n")
        (repo / "autodev_archive").mkdir()
        os.replace(repo / "old-roadmap.md", repo / "autodev_archive" / "old-roadmap.md")
        (repo / ".autodev" / "pipeline").mkdir(parents=True)
        (repo / ".autodev" / "pipeline" / "pipeline.json").write_text("{}\n")
        (repo / "src" / "myproject").mkdir(parents=True)
        (repo / "src" / "myproject" / "__init__.py").write_text("")
        assert _uncommitted_user_changes(str(repo)) == []

    def test_other_files_beside_lullabeast_ones_still_count(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "src" / "myproject").mkdir(parents=True)
        (repo / "src" / "myproject" / "__init__.py").write_text("")
        (repo / "src" / "myproject" / "core.py").write_text("x = 1\n")
        (repo / "docs").mkdir()
        (repo / "docs" / "prd.md").write_text("not the project PRD\n")
        assert sorted(_uncommitted_user_changes(str(repo))) == ["docs/prd.md", "src/myproject/core.py"]

    def test_not_a_repo_reads_as_unknown(self, tmp_path):
        assert _uncommitted_user_changes(str(tmp_path)) is None

    def test_windows_line_endings_alone_do_not_count(self, tmp_path):
        """Git for Windows checks text files out with CRLF (core.autocrlf=true in
        its system config, which the container doesn't have). Seen from the
        container, the index's stat data no longer matches (different inode and
        times), so git compares content, and every CRLF file reads as modified.
        The check folds line endings, so only a real edit counts."""
        repo = _repo(tmp_path)
        for name in ("README.md", "app.py"):
            (repo / name).unlink()
        _git(repo, "-c", "core.autocrlf=true", "checkout", "--", "README.md", "app.py")
        assert (repo / "README.md").read_bytes() == b"# Test\r\n"
        for name in ("README.md", "app.py"):  # same size, other stat data
            st = os.stat(repo / name)
            os.utime(repo / name, (st.st_atime + 5, st.st_mtime + 5))
        # The scenario holds: plain git, as inside the container, says modified.
        assert _git(repo, "status", "--porcelain").split() == ["M", "README.md", "M", "app.py"]

        assert _uncommitted_user_changes(str(repo)) == []
        (repo / "app.py").write_bytes(b"print('v2')\r\n")
        assert _uncommitted_user_changes(str(repo)) == ["app.py"]


class TestInitRefusesBeforeWriting:
    def test_refuses_with_the_paths_and_the_remedy(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        (repo / ".env").write_text("SECRET=1\n")
        head = _git(repo, "rev-parse", "HEAD")

        with patch("ui.server._atomic_symlink_swap"):
            result = _run_init_project(str(repo), VALID_ROADMAP_SEED,
                                       verification_content=VALID_VERIFICATION_CONTENT)

        assert result["ok"] is False
        assert ".env" in result["error"] and "app.py" in result["error"]
        assert "Commit, stash" in result["error"]
        # Nothing written, nothing committed, the work untouched.
        assert _git(repo, "rev-parse", "HEAD") == head
        assert not (repo / ".autodev").exists()
        assert not (repo / "roadmap.md").exists()
        assert (repo / "app.py").read_text() == "print('v2')\n"
        assert (repo / ".env").read_text() == "SECRET=1\n"

    def test_lists_five_paths_and_counts_the_rest(self, tmp_path):
        repo = _repo(tmp_path)
        for i in range(8):
            (repo / f"f{i}.txt").write_text("x\n")
        with patch("ui.server._atomic_symlink_swap"):
            result = _run_init_project(str(repo), VALID_ROADMAP_SEED,
                                       verification_content=VALID_VERIFICATION_CONTENT)
        assert result["ok"] is False
        assert "f0.txt, f1.txt, f2.txt, f3.txt, f4.txt and 3 more" in result["error"]

    def test_lullabeast_files_alone_launch_and_are_committed(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "roadmap.md").write_text(VALID_ROADMAP_SEED)
        (repo / ".gitignore").write_text("*.pyc\n")

        with patch("ui.server._atomic_symlink_swap"):
            result = _run_init_project(str(repo), VALID_ROADMAP_SEED,
                                       verification_content=VALID_VERIFICATION_CONTENT)

        assert result["ok"] is True, result
        assert _git(repo, "log", "-1", "--format=%s").strip() == "init: add pipeline project structure"
        assert _git(repo, "status", "--porcelain") == ""
        committed = set(_git(repo, "show", "--name-only", "--format=", "HEAD").split())
        assert {"roadmap.md", ".gitignore", "verification.md", "prd.md"} <= committed


class TestPreflightReportsIt:
    def _config(self, repo):
        """Minimal OpenClaw root (symlink + agent workspaces), as in
        test_api_setup_preflight.py, so the other checks find what they read."""
        oc = repo.parent / ".openclaw"
        oc.mkdir()
        (oc / "pipeline-project").symlink_to(repo)
        for agent in ("planner", "executor", "reviewer", "escalation"):
            ws = oc / f"workspace-{agent}"
            ws.mkdir()
            for doc in ("AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"):
                (ws / doc).write_text(f"# {doc}\n")
        pp = str(oc / "pipeline-project")
        config = {
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
        return config

    def _preflight(self, repo):
        from fastapi.testclient import TestClient
        from ui.server import app

        with patch("ui.server.load_config", return_value=self._config(repo)), \
             patch("ui.server.append_recent_project"):
            response = TestClient(app).post("/api/setup/preflight", json={"repo_path": str(repo)})
        assert response.status_code == 200
        rows = [c for c in response.json()["checks"] if c["check"] == "uncommitted changes"]
        assert len(rows) == 1, response.json()["checks"]
        return rows[0]

    def test_fails_with_the_paths(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        row = self._preflight(repo)
        assert row["status"] == "fail"
        assert "app.py" in row["message"] and "Commit, stash" in row["message"]

    def test_passes_on_a_clean_repo(self, tmp_path):
        assert self._preflight(_repo(tmp_path))["status"] == "pass"

    def test_shared_checks_leave_it_out(self, tmp_path):
        """Switch-project, revalidate and relaunch resume runs with uncommitted
        work in progress; the checks they share must not fail on it."""
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        checks = _run_preflight_checks(str(repo), config=self._config(repo))
        assert not [c for c in checks if c["check"] == "uncommitted changes"]


class TestQueueAddRefusesIt:
    """The Queue screen's Add dialog and the MCP queue_add tool post /api/queue/add
    without the Launch dialog's preflight, and a queued repo gets the same commits
    and resets once it starts."""

    def _add(self, repo, tmp_path):
        from fastapi.testclient import TestClient
        from ui.server import app

        config = TestPreflightReportsIt()._config(repo)
        config["pipeline_queue_path"] = str(tmp_path / "pipeline_queue.json")
        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server.append_recent_project"):
            return TestClient(app).post("/api/queue/add", json={"project_path": str(repo)})

    def test_refuses_with_the_paths_and_queues_nothing(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        (repo / "notes.txt").write_text("todo\n")
        head = _git(repo, "rev-parse", "HEAD")

        response = self._add(repo, tmp_path)

        assert response.status_code == 400
        errors = response.json()["validation_errors"]
        rows = [e for e in errors if e["check"] == "uncommitted changes"]
        assert len(rows) == 1, errors
        assert "app.py" in rows[0]["message"] and "notes.txt" in rows[0]["message"]
        assert "Commit, stash" in rows[0]["message"]
        assert not (tmp_path / "pipeline_queue.json").exists()
        assert _git(repo, "rev-parse", "HEAD") == head
        assert (repo / "app.py").read_text() == "print('v2')\n"

    def test_lists_only_the_failing_checks(self, tmp_path):
        """The Add dialog prints every row it gets in red, so passing rows would
        bury the one that matters."""
        repo = _repo(tmp_path)
        (repo / "app.py").write_text("print('v2')\n")
        errors = self._add(repo, tmp_path).json()["validation_errors"]
        assert errors and all(e["status"] == "fail" for e in errors), errors

    def test_a_clean_repo_is_not_refused_for_it(self, tmp_path):
        response = self._add(_repo(tmp_path), tmp_path)
        errors = response.json().get("validation_errors") or []
        assert not [e for e in errors if e["check"] == "uncommitted changes"], errors
