"""Phase 4 — parse-safety cluster: T4.3.

T4.3 — a corrupt or empty ``current_phase.json`` on the advance path must route
to the existing F4 escalation (``ERR_PHASE_RESOLVER_FAILED``) instead of crashing
with an unhandled ``JSONDecodeError`` or silently advancing to a blank phase.

(T4.9 — the llama baseUrl resolver — and T4.2 — the blame-analyst shape check —
were removed along with the orchestrator's direct LLM calls: blame attribution
is gone and the escalation advisory is agent-owned. See
``test_executor_exhaustion_escalates.py`` / ``test_escalation_advisory_agent_owned.py``.)
"""
import importlib
import json
import os
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
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, tmp_path


# ---------------------------------------------------------------------------
# T4.3 — a corrupt/empty current_phase.json on advance must escalate (F4),
# not crash with an unhandled JSONDecodeError or advance to a blank phase.
#
# _advance_to_next_pending_phase deletes current_phase.json at the top and the
# resolver re-writes it; so the mocked resolver writes the (bad) file as a side
# effect and reports a PENDING verdict.
# ---------------------------------------------------------------------------

class TestT43CurrentPhaseGuard:

    @staticmethod
    def _resolver_writes(mod, content):
        def _fake_run(cmd, **kwargs):
            if (isinstance(cmd, (list, tuple)) and len(cmd) >= 2
                    and str(cmd[-1]).endswith("phase_resolver.py")):
                with open(os.path.join(mod.PROJECT_ARTIFACTS_DIR, "current_phase.json"), "w") as f:
                    f.write(content)
                m = MagicMock()
                m.returncode = 0
                m.stdout = "PENDING: Phase 3 (CORE-2)"
                m.stderr = ""
                return m
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        return _fake_run

    def test_corrupt_phase_file_escalates(self, orch):
        """A truncated current_phase.json must route to F4 escalation, not raise
        an unhandled JSONDecodeError out of the advance helper."""
        inst, mod, _ = orch
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{ corrupt not json ")):
            sig = inst._advance_to_next_pending_phase(trigger="phase_complete")
        assert sig == "continue"
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"

    def test_empty_shape_phase_file_escalates(self, orch):
        """A valid-but-shapeless current_phase.json ({} — no raw_id) must escalate
        rather than silently advancing to current_phase=0, raw_id=''."""
        inst, mod, _ = orch
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{}")):
            sig = inst._advance_to_next_pending_phase(trigger="phase_complete")
        assert sig == "continue"
        assert inst.state["current_agent"] == "escalation"
        assert inst.state.get("current_phase_raw_id", "") == "", "must not advance to a blank phase"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"

    def test_startup_shapeless_phase_file_escalates(self, orch):
        """The startup resolver read (the roadmap's 'mirror') must also escalate on
        a shapeless current_phase.json instead of advancing blind to a 'phase/'
        branch with colliding session keys. (A corrupt file there is already caught
        by the helper's broad except; this covers the empty-shape gap.)"""
        inst, mod, _ = orch
        inst.state["current_agent"] = "planner"
        inst.state["current_phase"] = 0
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{}")):
            sig = inst._run_startup_planner_phase_zero_and_branch()
        assert sig == "enter_main_loop"
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
