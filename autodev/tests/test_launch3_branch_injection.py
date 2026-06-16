"""LAUNCH-3 — close the ``raw_id`` → git-branch command-injection path.

LAUNCH-0 spike 3 confirmed the vector: a roadmap phase id (``raw_id``, captured by
``phase_resolver`` as *any* non-backtick string) flowed unescaped into
``subprocess.run(f"git checkout {branch} 2>/dev/null || git checkout -b {branch}", shell=True)``,
so a phase header `` - [ ] `e1; touch INJECTED` | LOW | … `` would execute the
injected command. Roadmaps come from the converter LLM or a human author, so the
vector is reachable.

The fix closes it at BOTH ends:
  * source — ``phase_resolver`` rejects a *selected* ``raw_id`` outside
    ``[A-Za-z0-9._-]`` (exit 1 → the orchestrator routes to escalation).
  * sink   — the orchestrator checks out / creates the branch with **list-form**
    argv (no shell) via the shared ``_checkout_or_create_branch`` helper.

This file fails against the pre-LAUNCH-3 tree (the helper does not exist; the
resolver accepts the malicious id) and passes after.
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
GATE_DIR = os.path.join(PIPELINE_DIR, "gate_scripts")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
PHASE_RESOLVER_PATH = os.path.join(GATE_DIR, "phase_resolver.py")

for _p in (PIPELINE_DIR, GATE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orc_module  # noqa: E402
import phase_resolver as resolver_mod  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers (mirror test_orchestrator_phase_branch_guard.py)
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _current_branch(repo):
    return subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def _setup_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("init\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _make_orch(repo):
    with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
        orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
        orch.lock_fd = None
    return orch


_ROADMAP_TEMPLATE = "# Test Roadmap\n\n- [ ] `{pid}` | LOW | Build the thing\n> Exit: it works\n"


def _write_roadmap(tmp_path, pid):
    rm = tmp_path / "roadmap.md"
    rm.write_text(_ROADMAP_TEMPLATE.format(pid=pid))
    return rm


def _run_resolver(roadmap_path):
    return subprocess.run(
        [sys.executable, PHASE_RESOLVER_PATH, str(roadmap_path)],
        capture_output=True, text=True,
        env={**os.environ, "AUTODEV_REPO_PATH": REPO_ROOT},
    )


# --------------------------------------------------------------------------- #
# SINK — orchestrator._checkout_or_create_branch (list-form, no shell)
# --------------------------------------------------------------------------- #
class TestCheckoutOrCreateBranchSink:
    def test_checks_out_existing_branch(self, tmp_path):
        repo = _setup_repo(tmp_path)
        _git(repo, "checkout", "-b", "phase/CORE-1")
        _git(repo, "checkout", "main")
        orch = _make_orch(repo)
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            orch._checkout_or_create_branch("phase/CORE-1")
        assert _current_branch(repo) == "phase/CORE-1"

    def test_creates_branch_when_absent(self, tmp_path):
        repo = _setup_repo(tmp_path)
        orch = _make_orch(repo)
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            orch._checkout_or_create_branch("phase/CORE-2")
        assert _current_branch(repo) == "phase/CORE-2"

    def test_shell_metacharacters_do_not_execute(self, tmp_path):
        """Core security assertion: a ``;``-laden branch name never reaches a shell.

        Under the old shell=True idiom, ``git checkout phase/x; touch INJECTED …``
        runs ``touch INJECTED``. With list-form argv the whole string is one git
        argument: git rejects the invalid ref (raising, since ``check`` defaults
        True) and the injected command never executes.
        """
        repo = _setup_repo(tmp_path)
        orch = _make_orch(repo)
        sentinel = repo / "INJECTED"
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            with pytest.raises(subprocess.CalledProcessError):
                orch._checkout_or_create_branch("phase/x; touch INJECTED")
        assert not sentinel.exists(), "injected `touch INJECTED` executed — shell injection!"

    def test_check_false_is_best_effort(self, tmp_path):
        """The startup call site passed no check=True — check=False must not raise."""
        repo = _setup_repo(tmp_path)
        orch = _make_orch(repo)
        sentinel = repo / "INJECTED"
        with patch.object(orc_module, "SYMLINK_TARGET", str(repo)):
            orch._checkout_or_create_branch("phase/x; touch INJECTED", check=False)
        assert not sentinel.exists()


# --------------------------------------------------------------------------- #
# SOURCE — phase_resolver rejects an unsafe *selected* raw_id
# --------------------------------------------------------------------------- #
class TestPhaseResolverSource:
    def test_safe_regex_accepts_canonical_ids(self):
        for pid in ("CORE-1", "API-E2", "UI-10", "DATA-M1", "PREREQ-3", "core_logic.v2"):
            assert resolver_mod._PHASE_ID_SAFE_RE.match(pid), pid

    def test_safe_regex_rejects_metacharacters(self):
        for pid in ("e1; touch x", "a|b", "a`b`", "a$(b)", "a b", "{PHASE-ID}", "a&&b", ""):
            assert not resolver_mod._PHASE_ID_SAFE_RE.match(pid), pid

    def test_resolver_accepts_clean_id(self, tmp_path):
        rm = _write_roadmap(tmp_path, "CORE-1")
        r = _run_resolver(rm)
        assert r.returncode == 0, r.stderr + r.stdout
        assert "PENDING" in r.stdout
        assert (tmp_path / ".autodev" / "pipeline" / "current_phase.json").is_file()

    def test_resolver_rejects_injection_id(self, tmp_path):
        rm = _write_roadmap(tmp_path, "e1; touch INJECTED")
        r = _run_resolver(rm)
        assert r.returncode == 1, f"expected exit 1, got {r.returncode}: {r.stdout}{r.stderr}"
        # the resolver must not write current_phase.json for a rejected id …
        assert not (tmp_path / ".autodev" / "pipeline" / "current_phase.json").is_file()
        # … and must never itself shell out
        assert not (tmp_path / "INJECTED").exists()


# --------------------------------------------------------------------------- #
# REGRESSION GUARD — the shell=True git idiom is gone for good
# --------------------------------------------------------------------------- #
def test_orchestrator_has_no_shell_true_call():
    """No ``subprocess`` call in orchestrator.py uses ``shell=True`` — the injection vector.

    AST-based on purpose: it inspects real call keywords, not source prose. A
    substring search false-trips on ``_checkout_or_create_branch``'s docstring,
    which legitimately *describes* the old ``shell=True`` idiom it replaced.
    """
    import ast

    tree = ast.parse(Path(ORCHESTRATOR_PATH).read_text())
    shell_true_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
    ]
    assert not shell_true_calls, f"{len(shell_true_calls)} shell=True call(s) remain in orchestrator.py"


def test_checkout_helper_defined_and_wired():
    # helper defined (1) + wired at the three former shell sites (3) = >= 4 references
    src = Path(ORCHESTRATOR_PATH).read_text()
    assert src.count("_checkout_or_create_branch") >= 4, "helper not wired at all three sites"
