"""Tests for robust base-branch detection in orchestrator."""

import os
import sys
from unittest.mock import MagicMock

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_detect_base_branch_prefers_main(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"] and cmd[4].endswith("/main"):
            return _proc(returncode=0)
        return _proc(returncode=1)

    monkeypatch.setattr(orch_mod.subprocess, "run", fake_run)
    assert orch_mod._detect_base_branch("/tmp/repo") == "main"


def test_detect_base_branch_falls_back_to_master(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"] and cmd[4].endswith("/main"):
            return _proc(returncode=1)
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"] and cmd[4].endswith("/master"):
            return _proc(returncode=0)
        return _proc(returncode=1)

    monkeypatch.setattr(orch_mod.subprocess, "run", fake_run)
    assert orch_mod._detect_base_branch("/tmp/repo") == "master"


def test_detect_base_branch_uses_origin_head(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"]:
            return _proc(returncode=1)
        if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return _proc(returncode=0, stdout="refs/remotes/origin/develop\n")
        return _proc(returncode=1)

    monkeypatch.setattr(orch_mod.subprocess, "run", fake_run)
    assert orch_mod._detect_base_branch("/tmp/repo") == "develop"


def test_detect_base_branch_uses_git_config_default(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"]:
            return _proc(returncode=1)
        if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return _proc(returncode=1)
        if cmd == ["git", "config", "--get", "init.defaultBranch"]:
            return _proc(returncode=0, stdout="trunk\n")
        return _proc(returncode=1)

    monkeypatch.setattr(orch_mod.subprocess, "run", fake_run)
    assert orch_mod._detect_base_branch("/tmp/repo") == "trunk"


def test_detect_base_branch_returns_main_as_last_resort(monkeypatch):
    monkeypatch.setattr(orch_mod.subprocess, "run", lambda *args, **kwargs: _proc(returncode=1))
    assert orch_mod._detect_base_branch("/tmp/repo") == "main"
