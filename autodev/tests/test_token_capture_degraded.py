"""Degraded token-capture signal — integrity finding 1 (observability roadmap).

``_sum_session_tokens`` returns silent zeros when an attempt's session JSONL
is missing (or the session was never resolved, so the path is None). Those
zeros are indistinguishable from a genuinely free attempt, so a renamed
sessions dir — or a sessions.json lookup failure — would zero a phase's
durable token row with no trace beyond a stdout WARN. Same observable
failure as the historic usage-field-name bug (the ``_sum_session_tokens``
bug class).

Fix under test (METRICS-E1):
- ``_accumulate_role_tokens`` latches ``phase_state.token_capture_degraded``
  and emits one ``token_capture_warning`` pipeline event whenever an
  attempt's capture is degraded (path None, or path set but file missing).
- ``_write_canonical_metrics_row`` copies the flag onto the canonical row
  (``token_capture_degraded``, default False) so degraded-capture phases are
  identifiable from the durable metrics data alone.

Fixture pattern mirrors ``test_token_post_sentinel_recount.py`` (live
accumulator) and ``test_phase3_metrics_row_pain_signals.py`` (row writer).
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
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
        "current_agent": "reviewer",
        "current_phase": 2,
        "current_phase_raw_id": "CORE-1",
        "reviewer_retries": 0,
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
    event_mock = MagicMock()
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", event_mock)

    return inst, orch_mod, tmp_path, event_mock


def _openclaw_row(inp=0, out=0, total=0, cost=0.0):
    """Real OpenClaw session row shape: role + usage nested under message{}."""
    return json.dumps({
        "id": "msg",
        "type": "message",
        "timestamp": "2026-06-12T00:00:00Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input": inp, "output": out,
                "cacheRead": 0, "cacheWrite": 0,
                "totalTokens": total,
                "cost": {"total": cost},
            },
        },
    })


def _warning_calls(event_mock):
    return [c for c in event_mock.call_args_list if c.args[0] == "token_capture_warning"]


# ---------------------------------------------------------------------------
# Live accumulator — flag + event on degraded capture.
# ---------------------------------------------------------------------------

class TestDegradedDetection:

    def test_missing_session_file_sets_flag_and_emits_event(self, orch, tmp_path):
        inst, _, _, event_mock = orch
        ghost = str(tmp_path / "never-written.jsonl")

        inst._accumulate_role_tokens("reviewer", ghost)

        assert inst.read_phase_state().get("token_capture_degraded") is True
        (call,) = _warning_calls(event_mock)
        assert call.args[1] == "CORE-1"          # phase
        assert call.args[2] == "reviewer"        # agent role
        assert call.args[3]["reason"] == "missing_session_file"
        assert call.args[3]["session_jsonl"] == ghost

    def test_none_session_path_sets_flag_and_emits_event(self, orch):
        inst, _, _, event_mock = orch

        inst._accumulate_role_tokens("executor", None)

        assert inst.read_phase_state().get("token_capture_degraded") is True
        (call,) = _warning_calls(event_mock)
        assert call.args[2] == "executor"
        assert call.args[3]["reason"] == "no_session_path"
        assert call.args[3]["session_jsonl"] is None

    def test_existing_session_file_does_not_flag(self, orch, tmp_path):
        inst, _, _, event_mock = orch
        jsonl = tmp_path / "sess-a.jsonl"
        jsonl.write_text(_openclaw_row(inp=100, out=10, total=110) + "\n")

        inst._accumulate_role_tokens("planner", str(jsonl))

        ps = inst.read_phase_state()
        assert "token_capture_degraded" not in ps
        assert _warning_calls(event_mock) == []
        # Capture itself is unaffected.
        assert ps["planner_tokens_acc"]["total_tokens"] == 110

    def test_flag_latches_across_subsequent_good_attempts(self, orch, tmp_path):
        """One degraded attempt taints the phase's totals permanently — a later
        clean attempt must not clear the latch (the zeros are already baked in)."""
        inst, _, _, event_mock = orch
        inst._accumulate_role_tokens("executor", str(tmp_path / "gone.jsonl"))

        good = tmp_path / "sess-b.jsonl"
        good.write_text(_openclaw_row(inp=50, total=50) + "\n")
        inst._accumulate_role_tokens("executor", str(good))

        ps = inst.read_phase_state()
        assert ps.get("token_capture_degraded") is True
        assert ps["executor_tokens_acc"]["total_tokens"] == 50
        assert len(_warning_calls(event_mock)) == 1  # one event per degraded attempt


# ---------------------------------------------------------------------------
# Canonical metrics row — durable flag passthrough.
# ---------------------------------------------------------------------------

def _drive_writer(tmp_path, monkeypatch, phase_state_extra=None):
    """Seed phase_state.json, run _write_canonical_metrics_row, return the row.

    Copied (not imported) from ``test_phase3_metrics_row_pain_signals.py``'s
    idiom — test files stay self-contained.
    """
    import orchestrator as orch_mod

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()

    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))

    phase_state = {
        "executor_retries": 0,
        "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0,
        "reviewer_retries": 0,
        "planner_tokens_acc": {},
        "executor_tokens_acc": {},
        "reviewer_tokens_acc": {},
        "escalations": 0,
        "skill_injected": "core-logic",
    }
    if phase_state_extra:
        phase_state.update(phase_state_extra)
    (artifacts / "phase_state.json").write_text(json.dumps(phase_state))
    (artifacts / "current_phase.json").write_text(json.dumps({
        "raw_id": "CORE-E1",
        "detail": "Phase CORE-E1: bring up tasks view",
    }))

    orch_inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch_inst.state = {
        "current_phase_raw_id": "CORE-E1",
        "reviewer_retries": 0,
        "phase_start_time": None,
    }
    orch_inst._write_canonical_metrics_row()

    metrics_path = artifacts / "metrics.jsonl"
    assert metrics_path.exists(), "canonical row was not written"
    rows = [json.loads(l) for l in metrics_path.read_text().splitlines() if l.strip()]
    return rows[-1]


class TestMetricsRowFlag:

    def test_row_carries_degraded_flag_when_set(self, tmp_path, monkeypatch):
        row = _drive_writer(tmp_path, monkeypatch,
                            phase_state_extra={"token_capture_degraded": True})
        assert row["token_capture_degraded"] is True

    def test_row_defaults_degraded_flag_false(self, tmp_path, monkeypatch):
        row = _drive_writer(tmp_path, monkeypatch)
        assert row["token_capture_degraded"] is False
