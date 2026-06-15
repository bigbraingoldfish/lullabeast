"""Tests for host_probes.py — deterministic, timeout-bounded host capability probes.

These pin the PREREQ-2 contract:

  - ``probe(capability) -> {status, version?, detail, guidance?}`` with
    ``status in {found, missing, unknown}``.
  - A binary that is on PATH and answers ``--version`` → ``found`` + version.
  - A binary that is **not** on PATH (``FileNotFoundError``) → ``missing`` + guidance
    (the blockable signal PREREQ-3 turns into a Launch-gating row).
  - Any *inconclusive* outcome — timeout, non-zero/uninterpretable exit, or an
    unexpected exception — → ``unknown``. ``probe()`` never raises and never hangs.
  - Every subprocess call is bounded by ``PREREQ_PROBE_TIMEOUT`` (default 10s).

All subprocess calls are mocked, so the suite is fully offline and deterministic.
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import host_probes  # noqa: E402 - sys.path wired above (and by conftest)


def _proc(returncode=0, stdout="", stderr=""):
    """Build a CompletedProcess-like stub for subprocess.run mocking."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


@pytest.fixture(autouse=True)
def _clean_probe_env(monkeypatch):
    """Keep the timeout knob hermetic: a value sourced into the dev shell must
    not shadow the default-timeout assertions. Tests that exercise the override
    re-set it explicitly via monkeypatch.setenv."""
    monkeypatch.delenv("PREREQ_PROBE_TIMEOUT", raising=False)
    yield


# ---------------------------------------------------------------------------
# A. Found + version
# ---------------------------------------------------------------------------

def test_probe_git_found_returns_version(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "git"
        return _proc(returncode=0, stdout="git version 2.39.2\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("git")
    assert r["status"] == "found"
    assert "2.39.2" in r["version"]


def test_probe_node_found_returns_version(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "node"
        return _proc(returncode=0, stdout="v18.12.0\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("node")
    assert r["status"] == "found"
    assert "18.12.0" in r["version"]


def test_probe_python_found_uses_python3(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        return _proc(returncode=0, stdout="Python 3.11.4\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("python")
    assert r["status"] == "found"
    assert "3.11.4" in r["version"]
    # python3 must be tried before bare python (modern default).
    assert seen[0] == "python3"


# ---------------------------------------------------------------------------
# B. Missing — the load-bearing FileNotFoundError -> missing reconciliation
# ---------------------------------------------------------------------------

def test_probe_node_missing_when_not_on_path(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("node")
    assert r["status"] == "missing"
    assert r["guidance"]  # non-empty install guidance
    assert "install" in r["guidance"].lower()


def test_binary_on_path_unity_absent_is_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.binary_on_path("unity")
    assert r["status"] == "missing"
    assert r["guidance"]


def test_probe_unknown_capability_falls_back_to_binary_on_path(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    # An arbitrary declared-tool name (PREREQ-3 calls probe("unity6") directly).
    r = host_probes.probe("unity6")
    assert r["status"] == "missing"
    assert seen == ["unity6"]  # dispatched to binary_on_path("unity6")


# ---------------------------------------------------------------------------
# C. Timeout -> unknown, never raises
# ---------------------------------------------------------------------------

def test_probe_git_timeout_returns_unknown(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=10)

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("git")  # must not raise
    assert r["status"] == "unknown"
    assert "tim" in r["detail"].lower()  # "timed out" / "timeout"


# ---------------------------------------------------------------------------
# D. Crash / unexpected exception -> unknown, never raises
# ---------------------------------------------------------------------------

def test_probe_git_oserror_returns_unknown(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("git")
    assert r["status"] == "unknown"


def test_probe_git_unexpected_exception_returns_unknown(monkeypatch):
    # A non-OSError exception must still be swallowed (never-raises contract).
    def fake_run(cmd, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("git")  # must not raise
    assert r["status"] == "unknown"


# ---------------------------------------------------------------------------
# E. Uninterpretable exit -> unknown
# ---------------------------------------------------------------------------

def test_probe_git_nonzero_exit_returns_unknown(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _proc(returncode=127, stdout="")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("git")
    # Binary present but did not behave like a version check -> not found, not missing.
    assert r["status"] == "unknown"


# ---------------------------------------------------------------------------
# F. Bounded — the timeout kwarg is actually passed and is env-tunable
# ---------------------------------------------------------------------------

def test_probe_passes_timeout_kwarg(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _proc(returncode=0, stdout="git version 2.39.2\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    host_probes.probe("git")
    assert captured.get("timeout") == 10  # default


def test_probe_timeout_env_override(monkeypatch):
    monkeypatch.setenv("PREREQ_PROBE_TIMEOUT", "3")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _proc(returncode=0, stdout="git version 2.39.2\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    host_probes.probe("git")
    assert captured.get("timeout") == 3


def test_probe_timeout_env_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("PREREQ_PROBE_TIMEOUT", "5 min")  # non-numeric
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _proc(returncode=0, stdout="git version 2.39.2\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    host_probes.probe("git")
    assert captured.get("timeout") == 10  # parse failure -> default


# ---------------------------------------------------------------------------
# G. Status domain invariant
# ---------------------------------------------------------------------------

def test_status_always_in_domain(monkeypatch):
    def found(cmd, **k):
        return _proc(returncode=0, stdout="git version 2.39.2\n")

    def missing(cmd, **k):
        raise FileNotFoundError(cmd[0])

    def timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=10)

    def nonzero(cmd, **k):
        return _proc(returncode=2)

    def crash(cmd, **k):
        raise RuntimeError("x")

    for fake in (found, missing, timeout, nonzero, crash):
        monkeypatch.setattr(host_probes.subprocess, "run", fake)
        r = host_probes.probe("git")
        assert r["status"] in {"found", "missing", "unknown"}
        assert isinstance(r["detail"], str)


# ---------------------------------------------------------------------------
# H. Browser probe — none-found degrades to unknown (locked decision), not missing
# ---------------------------------------------------------------------------

def test_probe_browser_found(monkeypatch):
    def fake_run(cmd, **kwargs):
        # First browser candidate responds to --version.
        return _proc(returncode=0, stdout="Google Chrome 120.0.6099.109\n")

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("browser")
    assert r["status"] == "found"
    assert "120" in r["version"]


def test_probe_browser_none_on_path_is_unknown(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_probes.subprocess, "run", fake_run)
    r = host_probes.probe("browser")
    # PATH-based browser detection is unreliable (macOS .app, packaged installs),
    # so "none found" is inconclusive -> unknown, NOT a misleading "missing".
    assert r["status"] == "unknown"
    assert "path" in r["detail"].lower()
