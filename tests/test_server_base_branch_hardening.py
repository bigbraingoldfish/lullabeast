"""Server-side `_detect_base_branch` hardening (audit M3).

The server twin of the orchestrator's T4.6-hardened `_detect_base_branch`
runs on the `GET /api/state` hot path (the dashboard's frequent poll) and,
before this hardening, spawned unbounded git probes with no exception guard:
a wedged git or a dead network-mounted project dir blocked each request
indefinitely (stacked polls exhaust the FastAPI threadpool), and a missing
git binary turned every /api/state call into a 500.

Mirrors autodev/tests/test_defensive_p4_reset_failclosed.py::TestT46DetectBaseBranch.
"""

import subprocess
from unittest.mock import MagicMock, patch

import ui.server as server_mod


class TestServerDetectBaseBranchHardening:

    def test_missing_git_returns_main(self, tmp_path):
        """A missing git binary (FileNotFoundError) must fall back to 'main',
        not 500 every /api/state poll."""
        with patch.object(server_mod.subprocess, "run", side_effect=FileNotFoundError("git")):
            assert server_mod._detect_base_branch(str(tmp_path)) == "main"

    def test_wedged_git_timeout_returns_main(self, tmp_path):
        """A wedged git (TimeoutExpired) must fall back to 'main' rather than
        block the request thread indefinitely."""
        with patch.object(
            server_mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            assert server_mod._detect_base_branch(str(tmp_path)) == "main"

    def test_all_git_probes_bounded_by_timeout(self, tmp_path):
        """Every subprocess.run in the detection must carry a timeout= kwarg."""
        calls = []

        def _rec(*args, **kwargs):
            calls.append(kwargs)
            m = MagicMock()
            m.returncode = 1   # force every branch to be tried → reaches the "main" fallback
            m.stdout = ""
            return m

        with patch.object(server_mod.subprocess, "run", side_effect=_rec):
            result = server_mod._detect_base_branch(str(tmp_path))

        assert result == "main"
        assert calls, "expected git probes to run"
        assert all("timeout" in kw for kw in calls), (
            "every _detect_base_branch git probe must pass timeout=; "
            f"missing on {[i for i, kw in enumerate(calls) if 'timeout' not in kw]}"
        )

    def test_configured_base_branch_short_circuits_probes(self, tmp_path):
        """An explicit configured base branch is returned without any git call."""
        with patch.object(server_mod.subprocess, "run",
                          side_effect=AssertionError("no probe expected")):
            assert server_mod._detect_base_branch(str(tmp_path), "release") == "release"

    def test_detects_main_branch_normally(self, tmp_path):
        """Characterization: a present 'main' ref is returned on the first probe."""

        def _rec(cmd, **kwargs):
            m = MagicMock()
            # show-ref --verify refs/heads/main → success
            m.returncode = 0 if cmd[:3] == ["git", "show-ref", "--verify"] else 1
            m.stdout = ""
            return m

        with patch.object(server_mod.subprocess, "run", side_effect=_rec):
            assert server_mod._detect_base_branch(str(tmp_path)) == "main"
