"""Proactive phase branch integrity: _ensure_phase_branch() correctness and call sites.

The orchestrator can end up with HEAD on the base branch (main) after a RESET_PHASE
escalation command deletes the phase branch and checks out main.  If the phase branch is
never recreated before Phase 10 (the post-reviewer git commit+merge step), `git commit`
lands on main and the subsequent `git merge phase/CORE-N` fails with "not something we
can merge" because the branch either doesn't exist or has no unique commits.

Covers:
  - _ensure_phase_branch() method behaviour (real git repos, four states).
  - Call-site verification via AST: method must appear in the executor block, in the
    reviewer block, and before the Phase 10 `git add .` call.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orc_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _setup_repo(tmp_path: Path) -> Path:
    """Return a real git repo with one commit on main, ready for branching."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("init\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _make_orch(repo: Path):
    """Build a minimal Orchestrator instance with SYMLINK_TARGET pointing at repo."""
    with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
        from orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
    return orch


# ---------------------------------------------------------------------------
# Tests: _ensure_phase_branch() method behaviour
# ---------------------------------------------------------------------------

class TestEnsurePhaseBranchMethod:

    def test_noop_when_already_on_correct_branch(self, tmp_path):
        """HEAD is already on phase/CORE-E4 — returns True without checking out."""
        repo = _setup_repo(tmp_path)
        _git(repo, "checkout", "-b", "phase/CORE-E4")
        assert _current_branch(repo) == "phase/CORE-E4"

        orch = _make_orch(repo)
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            result = orch._ensure_phase_branch("phase/CORE-E4")

        assert result is True
        assert _current_branch(repo) == "phase/CORE-E4"

    def test_corrects_when_head_is_on_wrong_branch(self, tmp_path):
        """HEAD is on main but phase/CORE-E4 exists — checks out to phase/CORE-E4, returns True."""
        repo = _setup_repo(tmp_path)
        _git(repo, "checkout", "-b", "phase/CORE-E4")
        _git(repo, "checkout", "main")
        assert _current_branch(repo) == "main"

        orch = _make_orch(repo)
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            result = orch._ensure_phase_branch("phase/CORE-E4")

        assert result is True
        assert _current_branch(repo) == "phase/CORE-E4"

    def test_creates_branch_when_missing(self, tmp_path):
        """phase/CORE-E4 does not exist — creates it from current HEAD, returns True."""
        repo = _setup_repo(tmp_path)
        assert _current_branch(repo) == "main"
        # Verify the branch truly doesn't exist before calling
        branches = subprocess.run(
            ["git", "branch"], cwd=repo, capture_output=True, text=True
        ).stdout
        assert "phase/CORE-E4" not in branches

        orch = _make_orch(repo)
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            result = orch._ensure_phase_branch("phase/CORE-E4")

        assert result is True
        assert _current_branch(repo) == "phase/CORE-E4"

    def test_returns_false_when_checkout_is_impossible(self, tmp_path):
        """Both checkout attempts fail (e.g. locked index) — returns False."""
        repo = _setup_repo(tmp_path)
        orch = _make_orch(repo)

        original_run = subprocess.run

        def _failing_run(cmd, **kwargs):
            # Allow symbolic-ref to return "main" so the guard knows correction is needed
            if isinstance(cmd, list) and cmd[:2] == ["git", "symbolic-ref"]:
                m = MagicMock()
                m.returncode = 0
                m.stdout = "main"
                return m
            # Fail all checkout attempts
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(128, cmd)
            m = MagicMock()
            m.returncode = 128
            return m

        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            with patch.object(orc_module.subprocess, "run", _failing_run):
                result = orch._ensure_phase_branch("phase/CORE-E4")

        assert result is False


# ---------------------------------------------------------------------------
# Tests: call-site verification via AST
# ---------------------------------------------------------------------------

def _parse_orchestrator():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        return f.read(), ast.parse(f.read())


def _find_ensure_calls_in_agent_block(agent_name: str) -> list:
    """Return line numbers of _ensure_phase_branch calls inside the named agent elif block."""
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    ensure_lines = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_block = False

        def visit_If(self, node):
            test = node.test
            is_target = False
            if isinstance(test, ast.Compare) and isinstance(test.ops[0], ast.Eq):
                left, comps = test.left, test.comparators
                if (
                    (isinstance(left, ast.Constant) and left.value == agent_name)
                    or (
                        len(comps) == 1
                        and isinstance(comps[0], ast.Constant)
                        and comps[0].value == agent_name
                    )
                ):
                    is_target = True
            if is_target:
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        func = child.func
                        name = (
                            func.attr if isinstance(func, ast.Attribute)
                            else (func.id if isinstance(func, ast.Name) else "")
                        )
                        if name == "_ensure_phase_branch":
                            ensure_lines.append(child.lineno)
                self.generic_visit(node)
            else:
                self.generic_visit(node)

    Visitor().visit(tree)
    return ensure_lines


def test_ensure_phase_branch_called_in_executor_block():
    """_ensure_phase_branch must appear inside the executor elif block.

    After a RESET_PHASE, HEAD is on the base branch with the phase branch deleted.
    The executor is then invoked on the base branch.  The guard catches this drift
    before the webhook fires so the executor's commits land on the right branch.
    """
    lines = _find_ensure_calls_in_agent_block("executor")
    assert lines, (
        "_ensure_phase_branch is not called in the executor block of orchestrator.py. "
        "After a RESET_PHASE the phase branch is deleted and HEAD lands on main. "
        "Without this guard the executor runs on the wrong branch."
    )


def test_ensure_phase_branch_called_in_reviewer_block():
    """_ensure_phase_branch must appear inside the reviewer elif block.

    Phase 10 (post-reviewer git commit+merge) lives inside the reviewer block.
    The guard must be called before `git add .` so the commit lands on the
    phase branch, not on main.
    """
    lines = _find_ensure_calls_in_agent_block("reviewer")
    assert lines, (
        "_ensure_phase_branch is not called in the reviewer block of orchestrator.py. "
        "Phase 10 runs inside this block; without the guard, `git commit` may land "
        "on main when HEAD drifted after a RESET_PHASE."
    )


def test_ensure_phase_branch_precedes_git_add_in_reviewer_block():
    """Inside the reviewer block, _ensure_phase_branch must appear before `git add .`.

    The guard must run before any file staging so the commit that follows is
    guaranteed to target the phase branch, not the base branch.
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    ensure_lines = []
    git_add_lines = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False

        def visit_If(self, node):
            test = node.test
            is_reviewer = False
            if isinstance(test, ast.Compare) and isinstance(test.ops[0], ast.Eq):
                left, comps = test.left, test.comparators
                if (
                    (isinstance(left, ast.Constant) and left.value == "reviewer")
                    or (
                        len(comps) == 1
                        and isinstance(comps[0], ast.Constant)
                        and comps[0].value == "reviewer"
                    )
                ):
                    is_reviewer = True
            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                for child in ast.walk(node):
                    if not isinstance(child, ast.Call):
                        continue
                    func = child.func
                    name = (
                        func.attr if isinstance(func, ast.Attribute)
                        else (func.id if isinstance(func, ast.Name) else "")
                    )
                    if name == "_ensure_phase_branch":
                        ensure_lines.append(child.lineno)
                    if name == "run":
                        args = child.args
                        if (
                            args
                            and isinstance(args[0], ast.List)
                            and len(args[0].elts) >= 2
                            and isinstance(args[0].elts[0], ast.Constant)
                            and args[0].elts[0].value == "git"
                            and isinstance(args[0].elts[1], ast.Constant)
                            and args[0].elts[1].value == "add"
                        ):
                            git_add_lines.append(child.lineno)
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

    Visitor().visit(tree)

    assert ensure_lines, (
        "_ensure_phase_branch is not called anywhere in the reviewer block. "
        "It must be called before `git add .` in Phase 10."
    )
    assert git_add_lines, (
        "`git add .` not found in the reviewer block — Phase 10 structure may have changed."
    )
    # The first ensure call must appear before the (Phase 10) git add
    assert min(ensure_lines) < max(git_add_lines), (
        f"_ensure_phase_branch (first at line {min(ensure_lines)}) does not appear "
        f"before the Phase 10 `git add .` (at line {max(git_add_lines)}). "
        "The guard must precede staging to prevent commits landing on main."
    )
