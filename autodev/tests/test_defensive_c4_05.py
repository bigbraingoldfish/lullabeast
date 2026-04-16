"""C4-05: Remove ./ fallback in phase_init.py and phase_resolver.py.

CWD fallback silently writes metadata to the wrong tree when
_derive_pipeline_project() returns a path that doesn't exist.
The fix: fail fast with a clear path error instead of silently
using the current working directory.
"""
import os
import sys
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline", "gate_scripts")
PHASE_INIT = os.path.join(GATE_DIR, "phase_init.py")
PHASE_RESOLVER = os.path.join(GATE_DIR, "phase_resolver.py")


class TestC405NoCwdFallback:

    def test_phase_init_exits_nonzero_when_workspace_missing(self, tmp_path, monkeypatch):
        """phase_init must exit non-zero when derived workspace path doesn't exist."""
        env = os.environ.copy()
        # Point AUTODEV_REPO_PATH to a nonexistent path so _derive_pipeline_project
        # returns a path that doesn't exist
        env["AUTODEV_REPO_PATH"] = str(tmp_path / "nonexistent")
        env.pop("OPENCLAW_ROOT", None)
        env.pop("AUTODEV_PIPELINE_ROOT", None)

        result = subprocess.run(
            [sys.executable, PHASE_INIT],
            capture_output=True, text=True, env=env, cwd=str(tmp_path)
        )
        assert result.returncode != 0, (
            "phase_init should exit non-zero when derived workspace doesn't exist, "
            "not silently fall back to CWD. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_phase_resolver_exits_nonzero_when_roadmap_path_relative(self, tmp_path):
        """phase_resolver must exit non-zero when given a relative roadmap path.

        A relative path yields an empty dirname, which previously triggered the
        CWD fallback and would write current_phase.json to the wrong directory.
        """
        roadmap = tmp_path / "roadmap.md"
        roadmap.write_text(
            "- [ ] `CORE-1` | Phase 1 | Category: core | Exit: done\n"
        )

        env = os.environ.copy()
        env.pop("OPENCLAW_ROOT", None)
        env.pop("AUTODEV_PIPELINE_ROOT", None)

        # Pass a relative roadmap path — dirname will be empty
        result = subprocess.run(
            [sys.executable, PHASE_RESOLVER, "roadmap.md"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path)
        )
        assert result.returncode != 0, (
            "phase_resolver should exit non-zero when given a relative roadmap path "
            "with no directory component — the CWD fallback must be removed. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
