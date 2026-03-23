"""Tests for POST /api/setup/preflight endpoint and _run_preflight_checks helper."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


from ui.server import _run_preflight_checks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_git_repo(repo_path: Path, branch: str = "main"):
    """Create a minimal git repo structure (no actual git init, just dirs)."""
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)


def _make_gitignore(repo_path: Path, content: str = ""):
    (repo_path / ".gitignore").write_text(content)


def _all_gitignore_entries():
    return [
        "*.done",
        "phase_state.json",
        "planner_output.json",
        "executor_output.json",
        "reviewer_output.json",
        "escalation_output.json",
        "current_phase.json",
    ]


def _full_gitignore_content():
    return "\n".join(_all_gitignore_entries()) + "\n"


def _mock_subprocess_branch_pass(cmd, **kwargs):
    """Simulate subprocess.run for branch listing — returns output."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "  main\n"
    mock.stderr = ""
    return mock


def _mock_subprocess_remote_pass(url="https://github.com/example/repo.git"):
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        if "get-url" in cmd:
            mock.returncode = 0
            mock.stdout = url + "\n"
        elif "branch" in cmd:
            mock.returncode = 0
            mock.stdout = "  main\n"
        else:
            mock.returncode = 0
            mock.stdout = ""
        mock.stderr = ""
        return mock
    return _inner


def _mock_subprocess_remote_fail(cmd, **kwargs):
    """Simulate subprocess.run where remote get-url fails but branch listing passes."""
    mock = MagicMock()
    mock.stderr = ""
    if "get-url" in cmd:
        mock.returncode = 1
        mock.stdout = ""
    else:
        # branch --list
        mock.returncode = 0
        mock.stdout = "  main\n"
    return mock


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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

        entries_check = next(c for c in results if c["check"] == ".gitignore entries")
        assert entries_check["status"] == "pass"
        assert "All required entries present" in entries_check["message"]


class TestGitRepoCheck:

    def test_git_repo_missing_returns_fail(self, tmp_path):
        """No .git dir → 'git repo' check status 'fail'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        # No .git dir

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fail"

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
            if "branch" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # empty = no main/master
            else:
                mock.returncode = 0
                mock.stdout = "https://github.com/x/y.git\n"
            return mock

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=no_branch):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fail"

    def test_git_repo_unborn_main_branch_warns_not_fails(self, tmp_path):
        """Unborn main/master branch should warn with initial commit guidance."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def unborn_main(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # no listed main/master branch (unborn case)
            elif "symbolic-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "main\n"
            elif "get-url" in cmd:
                mock.returncode = 0
                mock.stdout = "https://github.com/x/y.git\n"
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=unborn_main):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "warn"
        assert "no commits yet" in git["message"].lower()
        assert "commit -m 'init'" in git["message"]


class TestGitRemoteCheck:

    def test_git_remote_missing_returns_warn(self, tmp_path):
        """When git remote get-url fails → 'git remote' check status 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_fail):
            results = _run_preflight_checks(str(repo_path))

        remote = next(c for c in results if c["check"] == "git remote")
        assert remote["status"] == "warn"


class TestRoadmapFileCheck:

    def test_roadmap_file_missing_returns_warn(self, tmp_path):
        """No *oadmap*.md file → 'roadmap file' check status 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        # No roadmap file

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

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

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

        roadmap = next(c for c in results if c["check"] == "roadmap file")
        assert roadmap["status"] == "pass"


class TestAllChecksInResponse:

    def test_all_checks_in_response(self, tmp_path):
        """Response includes check names covering symlink, .gitignore, git repo, workspace, git remote, roadmap."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

        check_names = [c["check"] for c in results]
        assert "symlink" in check_names
        assert ".gitignore" in check_names or ".gitignore entries" in check_names
        assert "git repo" in check_names
        assert any("workspace" in n for n in check_names)
        assert "git remote" in check_names
        assert "roadmap file" in check_names

    def test_each_check_has_required_fields(self, tmp_path):
        """Every check result has 'check', 'status', and 'message' fields."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            results = _run_preflight_checks(str(repo_path))

        for item in results:
            assert "check" in item
            assert "status" in item
            assert "message" in item
            assert item["status"] in ("pass", "fail", "warn")


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

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
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

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        assert response.status_code == 200
        for check in response.json()["checks"]:
            assert check["status"] in ("pass", "fail", "warn"), \
                f"Unexpected status '{check['status']}' for check '{check['check']}'"

    def test_endpoint_response_schema(self, tmp_path):
        """Response body conforms to {checks: [{check, status, message}]} schema."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_remote_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        data = response.json()
        assert "checks" in data
        for item in data["checks"]:
            assert set(item.keys()) >= {"check", "status", "message"}
