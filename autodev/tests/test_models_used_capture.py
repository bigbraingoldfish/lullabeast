"""MON-1 — durable per-role model record (``phase_state.models_used``).

The dashboard's Run Metrics header shows which skill ran a phase
(``skill_used``); the monitor redesign adds a matching **model badge**. The
capture mirrors the skill pattern: at every planner/executor/reviewer
invocation, ``_record_active_agent`` stamps the role's configured model
(``_get_agent_model``, reading ``openclaw.json``'s ``agents.list[].model``)
into ``phase_state.models_used`` ({role: model}); the canonical metrics row
copies it (``models_used``, null default for pre-deploy rows).

Best-effort telemetry: a missing/unconfigured model writes nothing and never
blocks the invocation.

Fixture pattern mirrors ``test_token_capture_degraded.py`` (live instance) and
``test_phase3_metrics_row_pain_signals.py`` (row writer).
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


def _agents_config(models):
    """openclaw.json-shaped agents.list with per-id primary models."""
    return {
        "hooks_url": "http://x",
        "hooks_token": "t",
        "pipeline": {},
        "agents": {"list": [
            {"id": aid, "model": {"primary": m}} for aid, m in models.items()
        ]},
    }


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
    inst.openclaw_config = _agents_config({
        "planner": "openrouter/minimax/minimax-m2.7",
        "executor": "llama-local/darkqwen3.6-27b-mtp",
        "reviewer": "llama-local/darkqwen3.6-27b-mtp",
    })
    inst.skill_manager = MagicMock()
    # The responseUsage pre-seed talks to the gateway — irrelevant here.
    inst._preset_session_response_usage = MagicMock()

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


class TestRecordRoleModel:

    def test_invocation_stamps_role_model(self, orch):
        inst, _, _ = orch
        inst._record_active_agent("executor", "pipeline:phase-2:core-1:executor-attempt-1")
        models = inst.read_phase_state().get("models_used")
        assert models == {"executor": "llama-local/darkqwen3.6-27b-mtp"}

    def test_roles_accumulate_and_reinvocation_overwrites(self, orch):
        inst, _, _ = orch
        inst._record_active_agent("planner", "k1")
        inst._record_active_agent("executor", "k2")
        # Model swap mid-phase (openclaw.json edit) — re-invocation records the new one.
        inst.openclaw_config = _agents_config({"executor": "openrouter/moonshotai/kimi-k2.6"})
        inst._record_active_agent("executor", "k3")
        models = inst.read_phase_state().get("models_used")
        assert models == {
            "planner": "openrouter/minimax/minimax-m2.7",
            "executor": "openrouter/moonshotai/kimi-k2.6",
        }

    def test_unconfigured_model_writes_nothing(self, orch):
        inst, _, _ = orch
        inst.openclaw_config = _agents_config({})  # role absent from agents.list
        inst._record_active_agent("reviewer", "k1")
        assert "models_used" not in inst.read_phase_state()

    def test_capture_failure_never_blocks_invocation(self, orch, monkeypatch):
        inst, _, _ = orch
        monkeypatch.setattr(inst, "_get_agent_model",
                            MagicMock(side_effect=RuntimeError("boom")))
        inst._record_active_agent("planner", "k1")  # must not raise
        assert inst._active_agent_role == "planner"


# ---------------------------------------------------------------------------
# Canonical metrics row — models_used passthrough.
# ---------------------------------------------------------------------------

def _drive_writer(tmp_path, monkeypatch, phase_state_extra=None):
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
        "raw_id": "CORE-E1", "detail": "Phase CORE-E1",
    }))

    orch_inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch_inst.state = {
        "current_phase_raw_id": "CORE-E1",
        "reviewer_retries": 0,
        "phase_start_time": None,
    }
    orch_inst._write_canonical_metrics_row()

    rows = [json.loads(l) for l in (artifacts / "metrics.jsonl").read_text().splitlines() if l.strip()]
    return rows[-1]


class TestMetricsRowModels:

    def test_row_carries_models_used(self, tmp_path, monkeypatch):
        models = {"planner": "m-a", "executor": "m-b", "reviewer": "m-b"}
        row = _drive_writer(tmp_path, monkeypatch,
                            phase_state_extra={"models_used": models})
        assert row["models_used"] == models

    def test_row_defaults_models_used_null(self, tmp_path, monkeypatch):
        row = _drive_writer(tmp_path, monkeypatch)
        assert row["models_used"] is None
