"""Tests for POST /api/setup/preflight endpoint and _run_preflight_checks helper."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Real subprocess.run before tests patch subprocess.run (avoids recursion in delegate).
_REAL_SUBPROCESS_RUN = subprocess.run


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


from ui.server import (
    _PIPELINE_GITIGNORE_ENTRIES,
    _preflight_materialize,
    _run_preflight_checks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_workspace(base_dir: Path, agent: str):
    """Create a complete workspace-{agent} directory with all required docs."""
    ws = base_dir / f"workspace-{agent}"
    ws.mkdir(parents=True, exist_ok=True)
    for doc in WORKSPACE_DOCS:
        (ws / doc).write_text(f"# {doc}\n")
    return ws


def _make_openclaw_dir(tmp_path: Path, repo_path: Path):
    """Create a minimal ~/.openclaw-like dir with symlink and all workspace dirs."""
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    # symlink pipeline-project → repo_path
    (openclaw / "pipeline-project").symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        _make_workspace(openclaw, agent)
    return openclaw


def _preflight_config(openclaw: Path, repo_path: Path) -> dict:
    """Symlink under openclaw_root (explicit config avoids real load_config()).

    Pipeline state files are pinned under the OpenClaw root by setting
    ``autodev_pipeline_root`` to the openclaw dir. The removed
    ``use_legacy_openclaw_runtime`` flag is intentionally not used.
    """
    pp = str(openclaw / "pipeline-project")
    oc = str(openclaw)
    return {
        "openclaw_root": oc,
        "project_dir_path": pp,
        "autodev_repo_path": str(repo_path),
        "autodev_pipeline_root": oc,
        "pipeline_state_path": os.path.join(oc, "pipeline_state.json"),
        "lock_path": os.path.join(oc, "pipeline.lock"),
        "pipeline_queue_path": os.path.join(oc, "pipeline_queue.json"),
        "events_path": os.path.join(oc, "pipeline_events.jsonl"),
        "ideas_dir": os.path.join(oc, "ideas"),
        "phase_state_path": os.path.join(pp, ".autodev", "pipeline", "phase_state.json"),
        "roadmap_path": os.path.join(pp, "roadmap.md"),
    }


def _make_git_repo(repo_path: Path, branch: str = "main"):
    """Create a minimal git repo structure (no actual git init, just dirs)."""
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)


def _make_gitignore(repo_path: Path, content: str = ""):
    (repo_path / ".gitignore").write_text(content)


def _all_gitignore_entries():
    return list(_PIPELINE_GITIGNORE_ENTRIES)


def _full_gitignore_content():
    return "\n".join(_all_gitignore_entries()) + "\n"


def _mock_subprocess_branch_pass(cmd, **kwargs):
    """Simulate subprocess.run for branch listing — returns output."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "  main\n"
    mock.stderr = ""
    return mock


def _mock_subprocess_preflight_pass():
    """Mock git subprocess calls used by preflight when no real git repo is needed."""
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.stderr = ""
        if not isinstance(cmd, list) or not cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and len(cmd) >= 2 and cmd[1] == "--version":
            mock.returncode = 0
            mock.stdout = "git version 2.40.0\n"
            return mock
        if "branch" in cmd and "--list" in cmd:
            mock.returncode = 0
            mock.stdout = "  main\n"
            return mock
        if "symbolic-ref" in cmd:
            mock.returncode = 0
            mock.stdout = "main\n"
            return mock
        if cmd[0] == "git" and "init" in cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and "-C" in cmd and "branch" in cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        mock.returncode = 0
        mock.stdout = ""
        return mock
    return _inner


def _delegate_git_subprocess_preflight(cmd, **kwargs):
    """Run real git for integration; fall back to mock for any other commands."""
    if isinstance(cmd, list) and cmd and cmd[0] == "git":
        return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
    return _mock_subprocess_preflight_pass()(cmd, **kwargs)


# ---------------------------------------------------------------------------
# Unit tests for _run_preflight_checks
# ---------------------------------------------------------------------------

class TestSymlinkCheck:

    def test_symlink_pass(self, tmp_path):
        """Symlink resolves to repo_path → check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "pass"

    def test_symlink_fail(self, tmp_path):
        """Symlink points elsewhere → auto-created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        wrong_path = tmp_path / "other"
        wrong_path.mkdir()
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()
        # Symlink points to wrong_path, not repo_path
        (openclaw / "pipeline-project").symlink_to(wrong_path)
        for agent in WORKSPACE_AGENTS:
            _make_workspace(openclaw, agent)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "fixed"
        assert str(repo_path) in sym["message"] or "Symlink" in sym["message"]

    def test_symlink_missing_returns_fail(self, tmp_path):
        """No symlink at all → created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()
        for agent in WORKSPACE_AGENTS:
            _make_workspace(openclaw, agent)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "fixed"


class TestGitignoreCheck:

    def test_gitignore_missing_returns_fail(self, tmp_path):
        """No .gitignore → created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        # No .gitignore

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        gi = next(c for c in results if c["check"] == ".gitignore")
        assert gi["status"] == "fixed"

    def test_gitignore_entries_injected(self, tmp_path):
        """When .gitignore is present but missing entries → entries auto-added, status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        # Only write one entry so others are missing
        _make_gitignore(repo_path, "*.done\n")
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        entries_check = next(c for c in results if c["check"] == ".gitignore entries")
        assert entries_check["status"] == "fixed"
        assert "Added" in entries_check["message"]

    def test_gitignore_entries_all_present(self, tmp_path):
        """When .gitignore has all required entries → message is 'All required entries present'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        entries_check = next(c for c in results if c["check"] == ".gitignore entries")
        assert entries_check["status"] == "pass"
        assert "All required entries present" in entries_check["message"]


class TestGitRepoCheck:

    def test_git_repo_missing_auto_inits(self, tmp_path):
        """No .git dir → preflight runs git init and reports 'git repo' fixed."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        # No .git dir — use real git for init/branch

        with patch("subprocess.run", side_effect=_delegate_git_subprocess_preflight):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fixed"
        assert (repo_path / ".git").is_dir()

    def test_git_repo_no_main_or_master_fails(self, tmp_path):
        """A .git dir exists but branch listing returns empty → 'git repo' check 'fail'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def no_branch(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # empty = no main/master
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd:
                mock.returncode = 1
                mock.stdout = ""
                return mock
            if isinstance(cmd, list) and "symbolic-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "phase/foo\n"
                return mock
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("subprocess.run", side_effect=no_branch):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fail"
        assert "no commits" in git["message"].lower() or "main" in git["message"].lower()

    def test_git_repo_unborn_main_branch_attempts_commit_fixed(self, tmp_path):
        """Unborn main/master branch: preflight attempts initial commit → 'fixed'.

        ISSUE-5: previously this case emitted 'warn' and asked the operator to
        commit manually. Now preflight runs git add -A + git commit (mirroring the
        fresh-init path) and emits 'git initial commit' / 'fixed' on success.
        """
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def unborn_main(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # no listed main/master branch (unborn case)
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd:
                mock.returncode = 1
                mock.stdout = ""
                return mock
            if "symbolic-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "main\n"
                return mock
            # add / commit succeed (simulates ISSUE-5 fix path)
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("subprocess.run", side_effect=unborn_main):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        init_check = next(c for c in results if c["check"] == "git initial commit")
        assert init_check["status"] == "fixed"
        assert "phase_base_commit" in init_check["message"].lower() or "head" in init_check["message"].lower()

    def test_git_repo_phase_only_branch_warns_when_head_has_commits(self, tmp_path):
        """Repo with commits on phase/* (no main/master) → warn, not fail."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def phase_only(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd and "--verify" in cmd:
                mock.returncode = 0
                mock.stdout = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd and "--abbrev-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "phase/foo\n"
                return mock
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("subprocess.run", side_effect=phase_only):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "warn"
        assert "phase branches" in git["message"].lower() or "main" in git["message"].lower()


class TestGitExecutableCheck:

    def test_git_version_passes(self, tmp_path):
        """git --version succeeds → 'git' check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        git = next(c for c in results if c["check"] == "git")
        assert git["status"] == "pass"


class TestRoadmapFileCheck:

    def test_roadmap_file_missing_returns_warn(self, tmp_path):
        """No *oadmap*.md file → 'roadmap file' check status 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        # No roadmap file

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        roadmap = next(c for c in results if c["check"] == "roadmap file")
        assert roadmap["status"] == "warn"

    def test_roadmap_file_present_returns_pass(self, tmp_path):
        """A file matching *oadmap*.md → 'roadmap file' check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        roadmap = next(c for c in results if c["check"] == "roadmap file")
        assert roadmap["status"] == "pass"


_OPENCLAW_ROOT_MSG_HINT = " (under OPENCLAW_ROOT)"


class TestWorkspaceCheckMessagesH29:
    """H-29: workspace-* preflight messages clarify location (OPENCLAW_ROOT)."""

    def test_pass_workspace_rows_include_openclaw_root_hint(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        for c in results:
            if c["status"] == "pass" and c["check"].startswith("workspace-") and "/" not in c["check"]:
                assert _OPENCLAW_ROOT_MSG_HINT in c["message"], c

    def test_missing_workspace_dir_message_includes_openclaw_root_hint(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        shutil.rmtree(openclaw / "workspace-planner")
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        miss = next(c for c in results if c["check"] == "workspace-planner" and c["status"] == "fail")
        assert "directory missing" in miss["message"]
        assert _OPENCLAW_ROOT_MSG_HINT in miss["message"]

    def test_missing_workspace_doc_message_includes_openclaw_root_hint(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        (openclaw / "workspace-planner" / "AGENTS.md").unlink()
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        miss = next(
            c for c in results
            if c["check"] == "workspace-planner/AGENTS.md" and c["status"] == "fail"
        )
        assert "missing" in miss["message"].lower()
        assert _OPENCLAW_ROOT_MSG_HINT in miss["message"]


def test_pipeline_gitignore_entries_constant():
    assert _PIPELINE_GITIGNORE_ENTRIES == [".autodev/pipeline/"]


class TestPipelineArtifactsDirPreflight:

    def test_preflight_creates_artifacts_dir(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        art = repo_path / ".autodev" / "pipeline"
        assert art.is_dir()
        row = next(c for c in results if c["check"] == "pipeline artifacts dir")
        assert row["status"] == "pass"

    def test_preflight_migrates_legacy_phase_state(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        (repo_path / "phase_state.json").write_text('{"escalation_resets": 0}')
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        assert not (repo_path / "phase_state.json").exists()
        assert (repo_path / ".autodev" / "pipeline" / "phase_state.json").exists()
        mig = [c for c in results if c["check"] == "pipeline artifacts migration"]
        assert mig and mig[0]["status"] == "fixed"


class TestAllChecksInResponse:

    def test_all_checks_in_response(self, tmp_path):
        """Response includes symlink, .gitignore, git cli, git repo, workspace, roadmap."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        check_names = [c["check"] for c in results]
        assert "symlink" in check_names
        assert "pipeline artifacts dir" in check_names
        assert ".gitignore" in check_names or ".gitignore entries" in check_names
        assert "git" in check_names
        assert "git repo" in check_names
        assert any("workspace" in n for n in check_names)
        assert "roadmap file" in check_names

    def test_each_check_has_required_fields(self, tmp_path):
        """Every check result has 'check', 'status', and 'message' fields."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        for item in results:
            assert "check" in item
            assert "status" in item
            assert "message" in item
            assert item["status"] in ("pass", "fail", "warn", "fixed")


# ---------------------------------------------------------------------------
# Integration tests for POST /api/setup/preflight endpoint
# ---------------------------------------------------------------------------

class TestPreflightEndpoint:

    def test_endpoint_returns_200(self, tmp_path):
        """POST with valid (patched) repo_path returns 200 with 'checks' array."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        assert response.status_code == 200
        data = response.json()
        assert "checks" in data
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) > 0

    def test_endpoint_requires_repo_path(self):
        """POST with empty repo_path returns 422."""
        client = load_server()
        response = client.post("/api/setup/preflight", json={"repo_path": ""})
        assert response.status_code == 422

    def test_endpoint_missing_repo_path_key(self):
        """POST with no repo_path key returns 422."""
        client = load_server()
        response = client.post("/api/setup/preflight", json={})
        assert response.status_code == 422

    def test_endpoint_checks_have_valid_statuses(self, tmp_path):
        """All check status values in the response are 'pass', 'fail', or 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        assert response.status_code == 200
        for check in response.json()["checks"]:
            assert check["status"] in ("pass", "fail", "warn", "fixed"), \
                f"Unexpected status '{check['status']}' for check '{check['check']}'"

    def test_endpoint_response_schema(self, tmp_path):
        """Response body conforms to {checks: [{check, status, message}]} schema."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        data = response.json()
        assert "checks" in data
        for item in data["checks"]:
            assert set(item.keys()) >= {"check", "status", "message"}


class TestPreflightMaterialize:
    """Unit tests for _preflight_materialize (roadmap/prd writes + conflicts)."""

    def test_writes_roadmap_when_missing(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        checks = _preflight_materialize(str(repo), VALID_ROADMAP_SEED, None)
        assert any(c["check"] == "roadmap write" and c["status"] == "fixed" for c in checks)
        assert (repo / "roadmap.md").read_text().strip() == VALID_ROADMAP_SEED.strip()

    def test_conflict_when_disk_differs(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "roadmap.md").write_text("# Other\n")
        checks = _preflight_materialize(str(repo), VALID_ROADMAP_SEED, None)
        assert any(c["check"] == "roadmap conflict" and c["status"] == "fail" for c in checks)

    def test_prd_conflict(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "prd.md").write_text("# A\n")
        checks = _preflight_materialize(str(repo), None, "# B\n")
        assert any(c["check"] == "prd conflict" and c["status"] == "fail" for c in checks)


class TestPreflightEndpointWithSeed:
    """POST /api/setup/preflight with roadmap_seed."""

    def test_writes_roadmap_via_seed(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()
        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post(
                "/api/setup/preflight",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 200
        assert (repo_path / "roadmap.md").read_text().strip() == VALID_ROADMAP_SEED.strip()
        names = [c["check"] for c in response.json()["checks"]]
        assert "roadmap seed" in names


# ---------------------------------------------------------------------------
# ISSUE-5: existing repo with no commits — unborn main branch
#
# When a .git dir already exists but the repo has zero commits (HEAD
# does not resolve), preflight must attempt to create an initial commit
# and report "fixed" or "fail" for the "git initial commit" check.
# The previous behaviour of emitting "warn" is intentional for the case
# where HEAD is absent; these tests assert the NEW behaviour and will
# FAIL against the current code until ISSUE-5 is implemented.
# ---------------------------------------------------------------------------


def _unborn_main_mock(*, add_rc=0, commit_rc=0, allow_empty_rc=0):
    """Factory for subprocess mock: existing .git, unborn main, configurable commit outcome."""
    def _inner(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        if not isinstance(cmd, list) or not cmd:
            return m
        # git --version
        if cmd[0] == "git" and len(cmd) >= 2 and cmd[1] == "--version":
            m.stdout = "git version 2.40.0\n"
            return m
        # branch --list main master → empty (no main/master yet)
        if "branch" in cmd and "--list" in cmd:
            m.stdout = ""
            return m
        # rev-parse --verify HEAD → fails (no commits)
        if "rev-parse" in cmd and "--verify" in cmd and "HEAD" in cmd:
            m.returncode = 1
            return m
        # symbolic-ref --short HEAD → "main" (unborn main branch)
        if "symbolic-ref" in cmd:
            m.stdout = "main\n"
            return m
        # git add -A
        if "add" in cmd and "-A" in cmd:
            m.returncode = add_rc
            return m
        # git commit --allow-empty (fallback — check before plain commit)
        if "commit" in cmd and "--allow-empty" in cmd:
            m.returncode = allow_empty_rc
            return m
        # git commit -m 'preflight: initial commit'  (first attempt)
        if "commit" in cmd:
            m.returncode = commit_rc
            return m
        return m
    return _inner


class TestGitUnbornMainCommitAttempt:
    """ISSUE-5: preflight must attempt a commit for existing repos with no HEAD."""

    def _make_env(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED)
        return repo_path, openclaw

    def test_unborn_main_commit_succeeds_reports_fixed(self, tmp_path):
        """When on unborn main with no commits, a successful commit → 'fixed'."""
        repo_path, openclaw = self._make_env(tmp_path)
        with patch("subprocess.run", side_effect=_unborn_main_mock(commit_rc=0)):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )
        init_check = next(
            (c for c in results if c["check"] == "git initial commit"), None
        )
        assert init_check is not None, (
            "Expected a 'git initial commit' check entry when unborn main has no commits. "
            "Got: " + str([c["check"] for c in results])
        )
        assert init_check["status"] == "fixed", (
            f"Expected status 'fixed' after successful commit, got {init_check['status']!r}. "
            "Current code emits 'warn' — ISSUE-5 not yet implemented."
        )

    def test_unborn_main_all_commits_fail_reports_fail(self, tmp_path):
        """When on unborn main and all commit attempts fail → 'fail' (not 'warn')."""
        repo_path, openclaw = self._make_env(tmp_path)
        with patch("subprocess.run", side_effect=_unborn_main_mock(
            commit_rc=1, allow_empty_rc=1
        )):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )
        init_check = next(
            (c for c in results if c["check"] == "git initial commit"), None
        )
        assert init_check is not None, (
            "Expected a 'git initial commit' check entry when all commits fail. "
            "Got: " + str([c["check"] for c in results])
        )
        assert init_check["status"] == "fail", (
            f"Expected status 'fail' after all commit attempts fail, got {init_check['status']!r}. "
            "Current code emits 'warn' — ISSUE-5 not yet implemented."
        )
        # The old 'git repo' warn must not also appear — fix replaces it
        git_repo_warns = [
            c for c in results
            if c["check"] == "git repo" and c["status"] == "warn"
            and "no commits yet" in c.get("message", "")
        ]
        assert not git_repo_warns, (
            "After ISSUE-5 fix the old 'no commits yet' warn should be replaced by "
            f"the 'git initial commit' fail entry. Still present: {git_repo_warns}"
        )

    def test_unborn_main_allow_empty_fallback_reports_fixed(self, tmp_path):
        """Regular commit fails (nothing to add) but --allow-empty succeeds → 'fixed'."""
        repo_path, openclaw = self._make_env(tmp_path)
        with patch("subprocess.run", side_effect=_unborn_main_mock(
            commit_rc=1, allow_empty_rc=0
        )):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )
        init_check = next(
            (c for c in results if c["check"] == "git initial commit"), None
        )
        assert init_check is not None, (
            "Expected a 'git initial commit' check when allow-empty commit succeeds."
        )
        assert init_check["status"] == "fixed", (
            f"Expected 'fixed' when allow-empty commit succeeds, got {init_check['status']!r}."
        )

    def test_repo_with_commits_no_initial_commit_check(self, tmp_path):
        """When HEAD resolves (repo has commits), no 'git initial commit' check is added."""
        repo_path, openclaw = self._make_env(tmp_path)

        def has_commits(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            if not isinstance(cmd, list):
                return m
            if "branch" in cmd and "--list" in cmd:
                m.stdout = "  main\n"
                return m
            return m

        with patch("subprocess.run", side_effect=has_commits):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )
        init_checks = [c for c in results if c["check"] == "git initial commit"]
        assert not init_checks, (
            "No 'git initial commit' check should be added when HEAD already resolves. "
            f"Got: {init_checks}"
        )


# ---------------------------------------------------------------------------
# Stage C — verification.md handling in preflight materialize + checks
# ---------------------------------------------------------------------------

class TestPreflightMaterializeVerification:
    """_preflight_materialize writes verification.md and validates its content."""

    def test_writes_verification_md_when_content_provided(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        checks = _preflight_materialize(
            str(repo),
            VALID_ROADMAP_SEED,
            None,
            VALID_VERIFICATION_CONTENT,
        )
        assert any(
            c["check"] == "verification write" and c["status"] == "fixed"
            for c in checks
        ), f"Expected verification write 'fixed'; got: {checks}"
        assert (repo / "verification.md").read_text().strip() \
            == VALID_VERIFICATION_CONTENT.strip()

    def test_invalid_verification_fails_materialize(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        # Missing the # Verification top heading → invalid
        bad = VALID_VERIFICATION_CONTENT.replace("# Verification\n\n", "", 1)
        checks = _preflight_materialize(
            str(repo),
            VALID_ROADMAP_SEED,
            None,
            bad,
        )
        assert any(
            c["check"] == "verification doc" and c["status"] == "fail"
            for c in checks
        ), f"Expected verification doc fail; got: {checks}"
        # And no file should be written when validation fails.
        assert not (repo / "verification.md").exists()

    def test_verification_conflict_when_disk_differs(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "verification.md").write_text("# Other content\n")
        checks = _preflight_materialize(
            str(repo),
            VALID_ROADMAP_SEED,
            None,
            VALID_VERIFICATION_CONTENT,
        )
        assert any(
            c["check"] == "verification conflict" and c["status"] == "fail"
            for c in checks
        ), f"Expected verification conflict; got: {checks}"


class TestRunPreflightChecksVerification:
    """_run_preflight_checks reports verification.md presence + validity."""

    def test_missing_verification_md_fails(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED)
        # No verification.md on disk

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        ver = [c for c in results if c["check"] == "verification doc"]
        assert ver, f"Expected verification doc check; got: {results}"
        assert ver[0]["status"] == "fail"
        assert "verification.md" in ver[0]["message"] or "Ideas screen" in ver[0]["message"]

    def test_valid_verification_md_passes(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED)
        (repo_path / "verification.md").write_text(VALID_VERIFICATION_CONTENT)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        ver = [c for c in results if c["check"] == "verification doc"]
        assert ver
        assert ver[0]["status"] == "pass"

    def test_invalid_verification_md_on_disk_fails(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED)
        # On-disk doc missing required section
        bad = VALID_VERIFICATION_CONTENT.replace(
            "## Verification stack\n- Acceptance tool: subprocess + assertions\n",
            "",
            1,
        )
        (repo_path / "verification.md").write_text(bad)

        with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(
                str(repo_path), config=_preflight_config(openclaw, repo_path)
            )

        ver = [c for c in results if c["check"] == "verification doc"]
        assert ver and ver[0]["status"] == "fail"


class TestPreflightEndpointVerification:
    """POST /api/setup/preflight accepts and plumbs verification_content."""

    def test_endpoint_writes_verification_md(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()
        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post(
                "/api/setup/preflight",
                json={
                    "repo_path": str(repo_path),
                    "roadmap_seed": VALID_ROADMAP_SEED,
                    "verification_content": VALID_VERIFICATION_CONTENT,
                },
            )

        assert response.status_code == 200
        assert (repo_path / "verification.md").read_text().strip() \
            == VALID_VERIFICATION_CONTENT.strip()
        names = [c["check"] for c in response.json()["checks"]]
        assert "verification write" in names or "verification doc" in names

    def test_endpoint_fails_with_old_format_roadmap(self, tmp_path):
        """Roadmap missing Behavioral Verification block fails preflight strictly."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        old_format = (
            "- [ ] `TEST-E1` | LOW | Do the thing\n"
            "  > Test: It works.\n"
        )
        client = load_server()
        with patch("ui.server.load_config", return_value=_preflight_config(openclaw, repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post(
                "/api/setup/preflight",
                json={
                    "repo_path": str(repo_path),
                    "roadmap_seed": old_format,
                    "verification_content": VALID_VERIFICATION_CONTENT,
                },
            )

        assert response.status_code == 200
        data = response.json()
        # roadmap seed should fail validation due to missing Behavioral Verification block
        assert any(
            c["status"] == "fail"
            and ("Behavioral Verification" in c["message"] or "roadmap" in c["check"].lower())
            for c in data["checks"]
        ), f"Expected failure for old-format roadmap; got: {data['checks']}"
