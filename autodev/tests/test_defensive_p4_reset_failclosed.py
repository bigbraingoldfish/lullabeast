"""Phase 4 — T4.6 (_detect_base_branch hardening) + T4.1 (reset fail-closed).

Red-first defensive tests for the orchestrator's recovery paths.

T4.6 — `_detect_base_branch` runs four `git` probes with no `timeout=` and no
guard for a missing/dangling `cwd`. A wedged git hangs the whole pipeline (the
exclusive lock is held, so heartbeat-cron cannot restart it) and a missing git
binary raises an uncaught `FileNotFoundError`. The hardened version bounds every
probe with a timeout and falls back to "main" on any of those failures.

T4.1 — `reset_phase` / `reset_execution` swallow a git `CalledProcessError` and
then unconditionally wipe outputs, zero counters, and transition to RUNNING — so
the operator's own recovery command "succeeds" while leaving a corrupt tree and
charging the reset budget. The hardened versions return success/failure and, on
git failure, route to escalation BEFORE any destructive step (and charge no
reset budget).
"""
import importlib
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Hermetic Orchestrator instance with all project/state paths under tmp_path.

    Uses ``__new__`` (not ``Orchestrator()``) because the real constructor can
    ``SystemExit`` late in the full suite via a leaked OPENCLAW_ROOT global.
    """
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING",
        "current_agent": "executor",
        "current_phase": 2,
        "current_phase_raw_id": "CORE-1",
        "phase_base_commit": "abc123",
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t", "pipeline": {}}
    inst.skill_manager = MagicMock()
    inst._current_attempt_retry_class = "initial_attempt"

    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    # Silence event emission unless a test asserts on it.
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, tmp_path


# ---------------------------------------------------------------------------
# T4.6 — _detect_base_branch must bound every probe and fail safe.
# ---------------------------------------------------------------------------

class TestT46DetectBaseBranch:

    def test_missing_git_returns_main(self, tmp_path):
        """A missing git binary (FileNotFoundError) must fall back to 'main',
        not propagate an uncaught exception into the reset path."""
        import orchestrator as orch_mod
        importlib.reload(orch_mod)
        with patch.object(orch_mod.subprocess, "run", side_effect=FileNotFoundError("git")):
            assert orch_mod._detect_base_branch(str(tmp_path)) == "main"

    def test_wedged_git_timeout_returns_main(self, tmp_path):
        """A wedged git (TimeoutExpired) must fall back to 'main' rather than
        hang the pipeline while the exclusive lock is held."""
        import orchestrator as orch_mod
        importlib.reload(orch_mod)
        with patch.object(
            orch_mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            assert orch_mod._detect_base_branch(str(tmp_path)) == "main"

    def test_all_git_probes_bounded_by_timeout(self, tmp_path):
        """Every subprocess.run in the detection must carry a timeout= kwarg."""
        import orchestrator as orch_mod
        importlib.reload(orch_mod)
        calls = []

        def _rec(*args, **kwargs):
            calls.append(kwargs)
            m = MagicMock()
            m.returncode = 1   # force every branch to be tried → reaches the "main" fallback
            m.stdout = ""
            return m

        with patch.object(orch_mod.subprocess, "run", side_effect=_rec):
            result = orch_mod._detect_base_branch(str(tmp_path))

        assert result == "main"
        assert calls, "expected git probes to run"
        assert all("timeout" in kw for kw in calls), (
            "every _detect_base_branch git probe must pass timeout=; "
            f"missing on {[i for i, kw in enumerate(calls) if 'timeout' not in kw]}"
        )

    def test_detects_main_branch_normally(self, tmp_path):
        """Characterization: a present 'main' ref is returned on the first probe."""
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        def _rec(cmd, **kwargs):
            m = MagicMock()
            # show-ref --verify refs/heads/main → success
            m.returncode = 0 if cmd[:3] == ["git", "show-ref", "--verify"] else 1
            m.stdout = ""
            return m

        with patch.object(orch_mod.subprocess, "run", side_effect=_rec):
            assert orch_mod._detect_base_branch(str(tmp_path)) == "main"


# ---------------------------------------------------------------------------
# T4.1 — reset_phase / reset_execution / nuclear_reset_phase must fail closed
# on a git error: escalate, preserve the tree, charge NO reset budget.
# ---------------------------------------------------------------------------

def _git_mock(fail_on_check):
    """subprocess.run stub. Raises CalledProcessError on check=True calls when
    fail_on_check is set (the reset's `git reset/checkout`); returns rc 0 for the
    no-check probes (_detect_base_branch, roadmap re-run)."""
    def _run(cmd, **kwargs):
        if fail_on_check and kwargs.get("check"):
            raise subprocess.CalledProcessError(128, cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m
    return _run


class TestT41ResetFailClosed:

    @staticmethod
    def _seed_output(mod, name="planner_output.json"):
        p = os.path.join(mod.PROJECT_ARTIFACTS_DIR, name)
        with open(p, "w") as f:
            f.write("{}")
        return p

    def test_reset_phase_git_fail_escalates_and_preserves_outputs(self, orch):
        inst, mod, _ = orch
        inst.state["current_agent"] = "executor"
        out = self._seed_output(mod)
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=True)):
            result = inst.reset_phase()
        assert result is False
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("last_error_code") == "ERR_RESET_PHASE_GIT_FAILED"
        assert os.path.exists(out), "outputs must NOT be wiped on a failed reset"

    def test_reset_phase_success_returns_true_and_wipes(self, orch):
        inst, mod, _ = orch
        out = self._seed_output(mod)
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=False)):
            result = inst.reset_phase()
        assert result is True
        assert not os.path.exists(out), "a successful reset wipes outputs"
        assert inst.state["current_agent"] == "planner"

    def test_reset_execution_escalation_git_fail_no_budget_charge(self, orch):
        inst, mod, _ = orch
        inst.write_phase_state_atomic({"escalation_resets": 1})
        inst.state["current_agent"] = "executor"
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=True)):
            result = inst.reset_execution("escalation")
        assert result is False
        assert inst.state["current_agent"] == "escalation"
        ps = inst.read_phase_state()
        assert ps.get("last_error_code") == "ERR_RESET_EXECUTION_GIT_FAILED"
        assert ps.get("escalation_resets") == 1, "must NOT charge escalation_resets on a failed reset"

    def test_reset_execution_auto_git_fail_no_retry_charge(self, orch):
        inst, mod, _ = orch
        inst.write_phase_state_atomic({"executor_retries": 0})
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=True)):
            result = inst.reset_execution("auto")
        assert result is False
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("executor_retries") == 0, "must NOT charge executor_retries"

    def test_nuclear_reset_git_fail_no_nuclear_charge(self, orch):
        inst, mod, _ = orch
        inst.write_phase_state_atomic({"nuclear_resets": 0})
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=True)):
            inst.nuclear_reset_phase()
        assert inst.state["current_agent"] == "escalation"
        ps = inst.read_phase_state()
        assert ps.get("nuclear_resets") == 0, "must NOT charge nuclear_resets on a failed reset"
        assert ps.get("last_error_code") == "ERR_RESET_PHASE_GIT_FAILED"

    def test_nuclear_reset_success_charges_once(self, orch):
        inst, mod, _ = orch
        inst.write_phase_state_atomic({"nuclear_resets": 0})
        with patch.object(mod.subprocess, "run", side_effect=_git_mock(fail_on_check=False)):
            inst.nuclear_reset_phase()
        assert inst.read_phase_state().get("nuclear_resets") == 1
        assert inst.state["current_agent"] == "planner"
